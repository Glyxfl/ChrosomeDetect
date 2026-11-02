# 部署与交付

## 1. 运行范围与依赖

统一项目是原生 HTML/JavaScript 网页 + Python HTTP 服务 + SQLite。本机默认 `127.0.0.1:8766`，前端无需 npm 构建。建议使用独立 Python 3.10 环境。当前本机在 Windows / helix Python 3.10 / PyTorch 2.5.1 上验证；Docker 配置已提供，但尚需在目标 Docker 主机实际构建验收。

本服务用于本机研究工作台，当前没有多用户登录与权限管理。Docker 仅映射宿主机回环地址。远程主机可通过 SSH 本地转发访问，不应把端口直接开放到公网。

## 2. Windows 本机

在源码根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r workbench/requirements.txt
.\.venv\Scripts\python.exe -m workbench.server --port 8766
```

本工作区也可双击 `启动统一分析工作台.cmd` 使用现有 helix 环境；其他机器可修改脚本中的 Python 路径或使用上面的命令。

打开 <http://127.0.0.1:8766>。健康接口为 `/api/health`；`available` 只代表权重文件存在，不保证模型已加载或效果合格，首次推理会加载并验证格式。

## 3. 模型文件

模型权重、原图、标注和数据库不进入 GitHub 源码仓库。请通过受控方式单独准备：

| 环境变量 | 含义 |
|---|---|
| `CLUSTER_WEIGHTS` | 染色体簇 YOLO 检测权重，当前已有 `chromosome_detector/weights/chromosome_cluster.pt` |
| `PHASE_WEIGHTS` | MI 分类权重，支持现有旧版及统一 pipeline 的 tiny-v1 |
| `MI_BACKBONE_WEIGHTS` | 旧版 MI 结构需要的 `yolov8n.pt`，新 tiny-v1 分类模型不需要 |
| `QUALITY_WEIGHTS` | 质量分类 tiny-v1 权重，默认 `workbench/data/models/quality.pt` |
| `INSTANCE_WEIGHTS` | 可选的 YOLO 单条染色体检测/分割权重；未配置时使用连通区域候选 |
| `OUTLIER_WEIGHTS` | 可选的正常对照图像特征 `baseline.npz`；未配置时按当前候选群体估计中心 |
| `CHROMOSOME_DATA` | 原图、数据库、任务记录、模型及训练产物根目录 |

PowerShell 示例：

```powershell
$env:CLUSTER_WEIGHTS='E:/models/chromosome_cluster.pt'
$env:PHASE_WEIGHTS='E:/models/mi_classifier.pt'
$env:MI_BACKBONE_WEIGHTS='E:/models/yolov8n.pt'
$env:QUALITY_WEIGHTS='E:/runtime/models/quality.pt'
$env:CHROMOSOME_DATA='E:/runtime'
python -m workbench.server
```

仅加载可信本地权重。没有质量模型时会明确回退到规则初筛，模型质量一致率不包含规则结果。若需要先验证模型预标注链路，可以执行：

```powershell
python -m workbench.bootstrap_quality --epochs 6
```

该命令仅使用程序生成的显微风格合成图，输出真实训练的实验模型、合成清单和合成测试报告，不读取业务数据、不生成业务人工真值。输出已经存在时拒绝覆盖，可用 `--output` 指定新路径。**合成测试指标不能证明真实图像性能**，界面与导出会保留 bootstrap 标记。正式部署应使用人工确认的真实质量数据运行 `workbench.pipeline` 替换模型。

单条染色体没有已验证的现成模型时，系统输出轮廓/框候选，粘连对象可能包含多条染色体。请补框、拆分、排除后确认。不能将候选区域数直接视为真实染色体数。

## 4. Docker（CPU 默认）

安装 Docker 后在根目录新建 `models` 文件夹，放入 `chromosome_cluster.pt`、`mi_classifier.pt`、`yolov8n.pt`。运行目录和模型不被源码允许列表跟踪：

```text
docker compose build
docker compose run --rm workbench python -m workbench.bootstrap_quality --epochs 6
docker compose up -d
docker compose logs -f
```

访问 <http://127.0.0.1:8766>。`runtime/` 为持久化数据卷，`models/` 为只读模型卷。镜像的 PyTorch 依赖可能较大，GPU 运行需目标主机另行准备匹配的驱动、容器运行时及 PyTorch 版本；本配置未声明 GPU 资源。

停止：`docker compose down`，保留 `runtime/` 与 `models/`。升级前停止写入并备份两个目录，再更新源码重建镜像。远程主机通过 `ssh -L 8766:127.0.0.1:8766 user@host` 转发后在本机访问。

## 5. 批量处理与人工关卡

导入按钮允许多选，每张独立保存并执行质量预标注，失败图像单独反馈。批量与指标页勾选图像后可对选中阶段提交批次，每批最多 200 张。单工作线程顺序执行，避免并发争抢 GPU。

批次不会替人确认上一步；未满足关卡的项目记为 `needs_review`，复核后重新提交。复核可确认、修正或驳回，修正/驳回要求原因和复核人。质量不可用可以确认归档，不能进入后续分析。每次修改保留原始模型快照、操作者、原因、时间及前后状态。

任务、批次和结果持久化在 `workbench.sqlite3`，重启将未完成任务标为中断失败，不会伪装完成或自动重复执行；用户可以重新提交。服务尚无取消和跨进程任务队列。

## 6. 指标口径

- 质量分级一致率 = 模型等级与人工最终等级一致张数 / 已确认且有有效模型快照的张数。批量模型指标排除规则结果，并单独报告合成初始化模型样本数。
- 簇检测与单条染色体候选分别计算；“识别准确率”在这里明确指 precision = TP/(TP+FP)，检出率为 recall = TP/(TP+FN)。使用 IoU ≥ 0.5、置信度排序、一对一匹配，不重复匹配真值。
- 染色体候选生成范围为人工确认的目标 MI 簇，不代表全切片所有染色体的总体检出率。完整真值需要同时补齐漏检、排除误检，确认时勾选“已完整核查本图全部目标”。没有勾选或没有预标注时显示不可计算；零分母输出 JSON null。
- 人工修正后的结果与原始预标注比较，属于复核符合性评估，不能视作盲法、独立测试集效果。正式独立测试由 `workbench.pipeline test` 和单独测试数据完成。
- 单张机器耗时为最近一次成功的各阶段耗时之和，包含图像解码与模型加载，排除队列、人工和空闲等待。质量初次导入也含原图保存时间。导出还保留每次重跑耗时、任务队列时间和失败任务记录。批量均值/机器吞吐量只统计完整确认的图像。

## 7. 辅助定位与导出

辅助定位是从结果跳回原图的导航：原图 → 簇 ID → 染色体候选 ID → 离群记录。网页可点选、缩放和平移；离群候选红色高亮，保留原图像素坐标和轮廓。人工补框支持指定所属 MI 簇。修改框时清除旧轮廓，避免错误地沿用原形状。

单张与批量 ZIP 包含：`manifest.json`、`aggregate_metrics.json`、汇总 `records.csv`，每图包含 `result.json`、`metrics.json`、`timings.json`、`audit.json`、`jobs.json`、`original.png`、`annotated.png`、簇及单条候选原分辨率裁剪。JSON 包含原始预标注、最终复核状态及模型来源。标注图是单独副本，不修改原图。CSV 中嵌套结构以 JSON 字符串保存。

导出不是训练数据集。正式训练前仍需审核标签、按患者/切片组划分，并转换成数据清单。SDPC/SDQC 全切片仍使用现有离线流式切块入口，未接入网页巨幅切片管理。

## 8. 验收与故障排查

```text
python -m unittest discover -s workbench/tests -v
python -m workbench.tests.http_smoke
node --check workbench/web/app.js
```

验证使用临时合成数据，覆盖 IoU 一对一匹配、漏检/误检统计、原始预标注与修正隔离、批次持久化、原图坐标转换、审计导出、并发版本冲突、训练与测试入口。真实模型推理和前端交互还需要目标环境实际验收。

端口冲突可改 `--port 8767`。权重未找到时检查环境变量和目录；不要上传权重到 Git。推理失败在批次记录显示具体原因，不会自动越过人工关卡。不可计算指标先检查是否有最新预标注、人工确认，以及完整真值勾选。备份时先停止服务，复制整个运行数据目录和独立模型目录；恢复时沿用相同模型和路径设置。

## 9. GitHub 源码发布

根目录 `.gitignore` 使用允许列表，只纳入工作台源码、文档、配置、启动与部署文件、CI，排除历史数据和结果目录；工作台内部继续排除运行数据、正式数据清单、权重、数据库及缓存。发布前检查 `git diff --cached --stat` 与 `git ls-files`。

在已确认的目标仓库与登录环境中，首次发布流程：

```text
git init -b main
git add README.md DEPLOYMENT.md .gitignore .dockerignore Dockerfile compose.yaml .github workbench
git commit -m "Build chromosome review and batch analysis workbench"
git remote add origin https://github.com/OWNER/REPOSITORY.git
git push -u origin main
```

启动脚本也应加入提交。仓库 URL、身份与认证由仓库所有者提供，不把令牌写进源码、命令参数或部署文档。正式图像与标注通过单独的数据管理流程交付。
