# 统一染色体分析工作台

网页入口为 `python -m workbench.server --port 8766`，打开 <http://127.0.0.1:8766>。Windows 工作区也可使用根目录启动脚本。环境、权重、Docker、指标口径、备份与 GitHub 发布流程见 [部署文档](../DEPLOYMENT.md)。

## 工作流程

1. 单张或批量上传图像，执行质量模型预标注，人工确认、修正或驳回。没有质量模型时明确使用规则，排除在模型一致率之外。不可用图片可确认归档，不能继续分析。
2. 检测染色体簇，查看裁剪并在原图定位。可增删、修改框、排除误检、补齐漏检。完整复核后勾选完整真值，输出簇准确率 precision 和检出率 recall。
3. 对簇进行 MI / 非 MI / 不可判读分类，确认目标 MI。
4. 在目标簇内提取具体染色体候选，保存原图坐标、轮廓、所属簇及 ID；可使用 `INSTANCE_WEIGHTS` 指定单条染色体 YOLO 检测/分割模型，未配置则使用需复核的连通区域算法。人工可选择所属簇、补框和排除误检，确认完整真值后计算候选识别指标。
5. 对具体候选提取离群分数。原图红色高亮离群候选、蓝色显示其余候选，点选结果可辅助定位。样本不足 3 个时只提供定位，标记需人工判断。复核为离群或正常后确认并导出。

单条候选可能是粘连或碎片，不是自动验证的真实染色体。当前离群阈值为稳健特征距离 ≥ 3，用于复核提示；不是生物学异常概率。也可通过 `OUTLIER_WEIGHTS` 接入正常对照特征基线。

## 数据与追溯

SQLite 持久保存项目、修改前后快照、任务及批次记录。每次推理保存独立原始预标注、模型路径/权重 SHA-256、运行编号；修改最终标注不会覆盖这些预测。确认、修正和驳回记录人名、原因和时间。这里的复核人是本机填写的标识，并非已认证账户。

`revision` 防止并发覆盖；上游修改会撤销下游确认、清空派生结果并将对应预测标记过期。更新预标注保留已经人工复核的对象；删除/驳回会留下审计记录。旧版簇级离群记录归入 `legacy_cluster_outliers`，不会被当作单条染色体异常显示。

批量页支持勾选 1–200 张按阶段排队，人工关卡不会被自动跳过；失败项和待复核项单独显示。任务重启中断会记录失败，需重新提交。

导出 JSON 或单张/批量 ZIP。ZIP 包含汇总指标、JSON/CSV、原图、高亮图、簇/候选裁剪、预测快照、审计与任务/耗时记录。原图不修改，所有坐标均对应 EXIF 校正后的原图像素。

指标没有真值时输出 null/待复核，不能解释为 0%。图像质量模型一致率仅统计已人工确认且未过期的模型预标注；规则与合成模型来源单独标识。precision 与 recall 基于 IoU ≥ 0.5 的一对一匹配。人工复核符合率不是独立测试性能。

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
| GET/POST `/api/projects` | 项目列表 / 原始二进制图片上传，X-Filename URI 编码 |
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

阶段名为 quality / clusters / phase / instances / outliers。上传最大 64 MB、6000 万像素，TIFF 仅读第一帧。SDPC/SDQC 巨幅全切片仍需既有离线切块流程。

## 验证

```text
python -m unittest discover -s workbench/tests -v
python -m workbench.tests.http_smoke
node --check workbench/web/app.js
```

测试使用临时合成图像与数据，不修改业务标注。单分类测试会在临时目录执行 1 epoch；合成测试只验证流程，不衡量真实图像效果。CPU/GPU 模型加载时间计入单张机器耗时，人工和队列等待另列或排除。
