# 统一染色体分析工作台

网页入口为 `python -m workbench.server --port 8766`，打开 <http://127.0.0.1:8766>。Windows 工作区也可使用根目录启动脚本。环境、权重、Docker、指标口径、备份与 GitHub 发布流程见 [部署文档](../DEPLOYMENT.md)。

## 工作流程

1. 点击“批量选择 SDPC 文件”多选文件，或“选择文件夹”递归选取其中的 SDPC；也可每行填写一个服务端本地路径，一次登记最多 200 条，免复制大文件。上传队列显示每个文件的进度、状态和失败原因，支持继续添加、失败单项/批量重试和 8 MiB 分块续传。上传完成即进入自动识别队列，一个文件失败不阻断其余文件；重复项会跳过。导入结束自动进行金字塔分块检测、跨块合并和 L0 无损簇图导出。
2. 切片任务中预览检出簇并在原切片定位，进入簇图质量及人工复核。没有质量模型时明确使用规则。质量不可用可归档，不能继续后续分析。簇图内可调整候选框、补齐漏检并排除误检；完整真值只针对该裁图，不等于全切片完整真值。
3. 对簇进行 MI / 非 MI / 不可判读分类，确认目标 MI。
4. 在目标簇内提取具体染色体候选，保存原图坐标、轮廓、所属簇及 ID；可使用 `INSTANCE_WEIGHTS` 指定单条染色体 YOLO 检测/分割模型，未配置则使用需复核的连通区域算法。人工可选择所属簇、补框和排除误检，确认完整真值后计算候选识别指标。
5. 对具体候选提取离群分数。原图红色高亮离群候选、蓝色显示其余候选，点选结果可辅助定位。样本不足 3 个时只提供定位，标记需人工判断。复核为离群或正常后确认并导出。

单条候选可能是粘连或碎片，不是自动验证的真实染色体。当前离群阈值为稳健特征距离 ≥ 3，用于复核提示；不是生物学异常概率。也可通过 `OUTLIER_WEIGHTS` 接入正常对照特征基线。

## 数据与追溯

SQLite 持久保存项目、修改前后快照、任务及批次记录。每次推理保存独立原始预标注、模型路径/权重 SHA-256、运行编号；修改最终标注不会覆盖这些预测。确认、修正和驳回记录人名、原因和时间。这里的复核人是本机填写的标识，并非已认证账户。

`revision` 防止并发覆盖；上游修改会撤销下游确认、清空派生结果并将对应预测标记过期。更新预标注保留已经人工复核的对象；删除/驳回会留下审计记录。旧版簇级离群记录归入 `legacy_cluster_outliers`，不会被当作单条染色体异常显示。

批量页支持勾选 1–200 张按阶段排队，人工关卡不会被自动跳过；失败项和待复核项单独显示。任务重启中断会记录失败，需重新提交。

导出 JSON 或单簇/批量 ZIP。ZIP 包含汇总指标、JSON/CSV、L0 原始簇图、高亮图、候选裁剪、预测快照、审计与任务/耗时记录。SDPC 来源额外包含 `source_sdpc.json` 和切片概览；原始 SDPC 单独流式下载，不默认重复塞入每个簇的 ZIP。记录同时保留簇图本地 `bbox` / `polygon` 与全切片 `bbox_l0` / `polygon_l0`，通过 `source_slide` 和 SHA-256 追溯来源。

原生 SDK 操作在独立进程运行。全切片 L0 不会整体解码进内存；检测读取 L1/L2 图块，簇图从原生 L0 重读。大于 6000 万像素的异常合并框会记录为导出失败，避免巨幅分配。浏览器缩放按需请求不超过 4096 单边原生像素的区域。

指标没有真值时输出 null/待复核，不能解释为 0%。图像质量模型一致率仅统计已人工确认且未过期的模型预标注；规则与合成模型来源单独标识。precision 与 recall 基于 IoU ≥ 0.5 的一对一匹配。人工复核符合率不是独立测试性能。

网页项目和批量指标默认仅使用 SDPC 来源的簇图，排除历史普通图片和合成演示项目。API 可用 `?source=sdpc` 限定同一范围；未加过滤参数的兼容接口仍返回全部历史项目。

## 训练与测试

```text
python -m workbench.pipeline validate --config workbench/configs/phase.json
python -m workbench.pipeline train --config workbench/configs/phase.json
python -m workbench.pipeline test --config workbench/configs/phase.json --weights path/to/best.pt
```

配置包含 clusters / phase / quality / outliers。分类提供 tiny-v1 CNN，检测使用 YOLO，离群训练拟合训练集正常对照特征。新机器需配置本地初始权重及路径。当前 `INSTANCE_WEIGHTS` 是外部已训练实例模型接入口，尚未提供实例分割模型的独立训练 CLI；本轮先接通实例候选、复核及评估。

分类数据清单格式：

```json
[{"image":"images/example.png","label":"mi","split":"train","group":"patient-or-slide-id","reviewed":true}]
```

实际清单包含 train / validation / test，必须人工确认。同一患者/切片组、同一图像内容不能跨划分；分类训练/验证覆盖所有类。检测 `label` 为 YOLO 标签文本路径。清单内路径相对清单目录，配置路径相对运行工作目录。示例文件仅示意格式，不是可用训练数据。

每次训练建立独立目录，保存配置、分组划分、历史及最优权重。测试仅使用指定权重的 test 划分，分类输出混淆矩阵及 precision/recall/F1，检测输出 YOLO 指标。测试集不用于分类最佳权重选择。工作台导出仍需审核裁剪、整理组别后转换成正式训练数据。

`python -m workbench.bootstrap_quality` 可用程序生成的合成图初始化质量分类器，明确标记 bootstrap。它不生成真实业务真值，不能替代人工标注真实图像。请用真实数据重新训练并评估。

## API

| 接口 | 用途 |
|---|---|
| GET `/api/health` | 模型文件可用状态 |
| GET `/api/projects` | 簇图复核项目列表 |
| POST `/api/slides/uploads` | 创建 SDPC 上传，name / size，默认上限 64 GiB |
| GET `/api/slides/uploads/:id` | 读取已接收字节数，供续传 |
| POST `/api/slides/uploads/:id/chunk` | 原始二进制分块，最大 8 MiB，X-Offset 为起点 |
| POST `/api/slides/uploads/:id/finish` | 完成上传并自动入队全片提簇 |
| POST `/api/slides/local` | 服务端已有 SDPC 路径 path，免复制并自动提簇 |
| POST `/api/slides/local-batch` | paths 数组，1–200 条路径，逐项登记、去重、隔离失败并自动提簇 |
| GET `/api/slides/import-batches/:id` | 持久保存的本地导入批次及逐文件扫描状态 |
| GET `/api/slides`、`/api/slides/:id` | 切片任务摘要 / 检出簇、来源和扫描范围 |
| POST `/api/slides/:id/run` | 对失败或中断的切片按原参数继续扫描 |
| GET `/api/slides/:id/overview` | 小尺寸金字塔概览 |
| GET `/api/slides/:id/region?box=x1,y1,x2,y2&level=N` | 原生金字塔区域，box 为 L0 坐标 |
| GET `/api/slides/:id/source`、`.../log`、`.../findings` | 原始 SDPC（支持 Range）/ 扫描日志 / 全切片异常定位坐标 |
| GET `/api/projects/:id` | 项目 JSON |
| POST `.../annotations` | 全量阶段标注，stage / rows / revision |
| POST `.../review` | row_id / decision(confirm,reject,correct) / label / reviewer / reason / stage / revision |
| POST `.../confirm` | 阶段确认，truth_complete 明确声明完整目标真值 |
| POST `.../run` | 异步阶段推理 |
| GET `/api/jobs/:id` | 任务状态、排队/计算耗时 |
| GET/POST `/api/batches` | 历史批次 / project_ids + stage 提交批次 |
| GET `/api/batches/:id` | 批次项目状态及失败原因 |
| GET `/api/metrics`、`.../:id/metrics` | 全部 / 单张指标 |
| GET `.../:id/image`、`.../:id/overlay` | 原图 / 高亮图 |
| GET `.../:id/crops/:object_id?full=1` | 原分辨率簇或具体候选裁剪 |
| GET `.../:id/export`、`.../:id/history` | 项目 / 修改审计 |
| GET `.../:id/bundle` | 单张完整 ZIP |
| GET `/api/bundle?id=ID1&id=ID2` | 批量完整 ZIP |

SDPC 主导入默认扫描整张切片，不接受 PNG/JPEG 替代原始输入。`/api/slides/local` 的 roi / limit 仅供明确的局部诊断，结果标记 `partial`。后续簇图阶段名为 quality / clusters / phase / instances / outliers。旧 `/api/projects` 的普通图片 POST 只保留为历史兼容和自动化合成夹具入口，不再用于网页主流程。

## 验证

```text
python -m unittest discover -s workbench/tests -v
python -m workbench.tests.http_smoke
node --check workbench/web/app.js
node --check workbench/web/slides.js
node workbench/tests/test_sdpc_upload.js
```

测试使用临时合成图像与数据，不修改业务标注。单分类测试会在临时目录执行 1 epoch；合成测试只验证流程，不衡量真实图像效果。CPU/GPU 模型加载时间计入单张机器耗时，人工和队列等待另列或排除。

真实 SDPC 小范围验收（必须显式指定 ROI，输出标记为局部，不触发整片测试）：

```text
python -m workbench.tests.sdpc_smoke --source path/to/slide.sdpc --output path/to/qa-runtime --roi X0 Y0 X1 Y1
```

该验收会比较导出的 L0 PNG 与原切片对应区域是否像素完全一致，并校验全片坐标映射。
