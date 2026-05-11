# 双光谱热红外与可见光三维点云生成程序

**项目目标**：用 DJI H30T 的可见光与热红外影像，生成带温度信息的三维点云。

整个处理流程从原始无人机影像到最终的热富集点云，共 9 个阶段：
1. 配置与元数据加载
2. 双光谱系统标定
3. 影像去畸变与预处理
4. 跨光谱匹配与单应性（TWMM）
5. 热红外辐射校正与温度提取
6. 多视角摄影测量重建（Metashape）
7. 可见光重投影与可见性判断
8. 点云热富集
9. 结果导出与质检报告

---

## 快速开始

### 前置要求

1. **Python 3.9+** 与 pip
2. **Agisoft Metashape Professional 2.2.1**（如果要执行阶段 6-8，即重建与富集）
3. **ExifTool 13.48+**（用于提取相机元数据）
4. **项目依赖包**

```bash
# 安装 Python 依赖
pip install -r requirements.txt
```

#### 关键说明

- **Metashape**：不通过 pip 安装，需独立下载桌面应用。该代码假设 Metashape Professional 已在系统中安装并授权。
- **ExifTool**：需要独立安装（Windows: 下载 exe；macOS/Linux: brew install exiftool）

### 数据准备

项目的标准目录结构应为：

```
workspace_root/
├── test_Arctic/              # 业务处理数据集
│   ├── rgb_dir/             # 可见光图像（RGB JPG/PNG）
│   ├── tiff_dir/            # 热红外 TIFF 主输入帧（推荐使用）
│   ├── thermal_dir/         # 热红外 JPG 预览（可选）
│   └── 航线参数.txt         # 飞行元数据
├── M400-H30T-CALIB-CHESSBOARD/  # 标定数据集
│   ├── RGB/                 # 棋盘格标定板的可见光图像
│   └── NIR/                 # 棋盘格标定板的热红外图像
├── H30T_RGB.xml             # RGB 初始标定参数
├── H30T_NIR.xml             # 热红外初始标定参数
├── TWMM-main/               # 跨光谱匹配库
├── Metashape/               # Metashape Python API 环境
├── metadata_all.json        # 相机元数据（需手动或 exiftool 生成）
└── main.py                  # 主入口脚本
```

### 生成 metadata_all.json

**metadata_all.json 需要手动生成**，方式如下：

```bash
# 使用 exiftool 提取所有影像的元数据
exiftool -json test_Arctic/rgb_dir/*.JPG > metadata_all.json
```

或同时提取热红外和可见光元数据：

```bash
exiftool -json test_Arctic/rgb_dir/*.JPG test_Arctic/tiff_dir/*.TIFF > metadata_all.json
```

**注意**：该文件记录了相机的焦距、感光度、曝光时间、传感器温度等关键参数，后续阶段（尤其是热辐射校正）依赖它。

### 执行全链路

```bash
# 默认使用 workspace_root 的数据进行完整处理
python main.py

# 或显式指定 run_all（效果相同）
python main.py run_all

# 指定自定义配置文件（JSON 格式）
python main.py --config-file path/to/custom_config.json
```

---

## 模块与工作流

### 0. 配置层（config/）

- **职责**：加载运行参数、管理工作区路径、生成可审计的运行清单
- **关键文件**：
  - `config_manager.py`：配置管理器，处理参数校验与路径规范化
  - `runtime_models.py`：数据模型定义（DatasetProfile, EnvironmentParameters 等）

### 1. 标定层（calibration/）

- **职责**：用棋盘格目标完成双光谱系统标定
- **输入**：`M400-H30T-CALIB-CHESSBOARD/RGB` 和 `M400-H30T-CALIB-CHESSBOARD/NIR`
- **输出**：相机矩阵、畸变系数、重投影误差、质量标记
- **方法**：OpenCV 棋盘格检测 + 相机标定（Zhang 2000）

### 2. 预处理层（preprocess/）

- **职责**：影像去畸变与前处理
- **输入**：原始 RGB/热红外影像 + 标定结果
- **输出**：几何校正后的图像
- **处理**：
  - RGB：CLAHE 对比度归一化
  - 热红外：灰度反转 + Otsu 自动二值化 + 局部对比度增强

### 3. 匹配层（matching/）

- **职责**：跨光谱特征匹配与单应性估计（基于 TWMM）
- **输入**：去畸变后的 RGB 与热红外图像对
- **输出**：对应点、异常值剔除结果、单应性矩阵
- **核心文件**：`twmm_adapter.py`（TWMM 的包装接口）

### 4. 热辐射层（radiometry/）

- **职责**：从热红外 TIFF 提取温度矩阵
- **输入**：热红外 TIFF 主帧 + metadata_all.json + 环境参数
- **输出**：温度矩阵（°C 或 K）+ 元数据记录
- **依赖**：DJI SDK、ExifTool 字段、环境温度、相对湿度

### 5. 重建层（Metashape/photogrammetry.py）

- **职责**：多视角立体重建生成稠密点云
- **输入**：RGB 影像组 + 内参
- **输出**：三维点云、相机姿态、深度图
- **流程**：特征匹配 → 相机对齐 → 深度图构建 → 稠密云生成

### 6. 重投影层（geometry/reprojection_export.py）

- **职责**：建立 3D 点与 2D 图像的对应关系
- **输入**：点云 + Metashape 项目 + RGB 标定
- **输出**：重投影坐标、误差、可见性标记

### 7. 热富集层（enrichment/thermal_enrichment.py）

- **职责**：为点云赋予温度属性
- **输入**：重投影记录 + 温度矩阵 + 单应性矩阵
- **输出**：带温度的点云 (XYZ, RGB, Temperature)
- **策略**：双线性插值 + 可见性过滤 + 多视图融合

### 8. 导出层（validation/ 和 pipeline_io/）

- **职责**：点云导出与质检报告生成
- **输出**：PLY/LAS 格式点云、匹配对叠加图、温度分布直方图、质量评分

---

## 常见参数与配置

### 配置文件示例（custom_config.json）

```json
{
  "workspace_root": "/path/to/workspace",
  "input_dataset_root": "/path/to/workspace/test_Arctic",
  "calibration_dataset_root": "/path/to/workspace/M400-H30T-CALIB-CHESSBOARD",
  "metadata_json": "/path/to/workspace/metadata_all.json",
  "stage_selection": ["calibration", "preprocess", "matching", "radiometry", "metashape", "geometry", "enrichment", "export"],
  "photogrammetry": {
    "downscale_align": 1,
    "downscale_depth": 4
  },
  "environment": {
    "air_temperature_celsius": 25.0,
    "relative_humidity_percent": 50.0
  }
}
```

### 常用命令行参数

- `--config-file`：指定配置 JSON 文件路径

### 跳过特定阶段

在配置中修改 `stage_selection` 数组，只包含需要执行的阶段名称。例如，仅执行标定和预处理：

```json
"stage_selection": ["calibration", "preprocess"]
```

---

## 输出结构

每次执行会在 `runs/` 目录下生成以下结构：

```
runs/
└── test_Arctic__<timestamp>__<checksum>/
    ├── calibration/       # 标定结果与内参
    ├── preprocess/        # 去畸变图像
    ├── matching/          # 匹配结果与单应性矩阵
    ├── radiometry/        # 温度矩阵与辐射元数据
    ├── metashape/         # 点云与相机姿态
    ├── geometry/          # 重投影记录
    ├── enrichment/        # 热富集点云
    ├── reports/           # 质检报告与统计
    └── manifest/          # 阶段执行清单与日志
```

---

## 故障排查

| 问题 | 原因 | 解决方案 |
|------|------|--------|
| `ModuleNotFoundError: No module named 'Metashape'` | Metashape 未安装或未授权 | 安装 Metashape Professional 2.2.1 并授权 |
| `FileNotFoundError: metadata_all.json` | 元数据文件缺失 | 运行 `exiftool -json test_Arctic/rgb_dir/*.JPG > metadata_all.json` |
| `棋盘格检测率低` | 标定板图像质量差 | 检查 `M400-H30T-CALIB-CHESSBOARD/` 中的图像是否清晰 |
| 匹配点数过少 | 可见光与热红外光谱差异大或 TWMM 参数不当 | 调整 TWMM 参数或检查输入图像质量 |
| 点云密度低 | RGB 特征不足或重建参数不当 | 调整 `downscale_align` 和 `downscale_depth` 参数 |
| 温度值异常（超出范围） | 环境参数设置错误或元数据缺失 | 检查 metadata_all.json 和环保参数配置 |

---

## 文档索引

- [AGENTS.md](AGENTS.md) - 全局设计统治文档
- [docs/architecture.md](docs/architecture.md) - 系统架构与数据流
- [docs/dataset_profile.md](docs/dataset_profile.md) - 数据集物理事实与基线参数
- [docs/runtime_config.md](docs/runtime_config.md) - 运行时配置与参数规范
- [docs/file_formats.md](docs/file_formats.md) - 数据格式与契约定义
- [docs/calibration_model.md](docs/calibration_model.md) - 标定模型
- [docs/matching_algorithm.md](docs/matching_algorithm.md) - 匹配与单应性算法
- [docs/radiometry_model.md](docs/radiometry_model.md) - 热辐射校正模型
- [docs/reconstruction_and_enrichment.md](docs/reconstruction_and_enrichment.md) - 重建与点云富集

---

## 许可证与引用

本项目集成了多个开源库与学术方法。核心匹配算法基于 TWMM（可见光-热红外匹配论文）。请参考 [TWMM-main/README.md](TWMM-main/README.md) 了解相关论文与引用信息。
- --search-radius
- --level-max
- --crop-width
- --crop-height
- --crop-offset-x
- --crop-offset-y
- --thermal-scale
- --visible-scale
- --outlier-threshold-px
- --min-inliers
- --homography-condition-max
- --max-pairs

### 2.3 radiometry

radiometry 的命令行参数也有强制必填项：

- --workspace-root
- --metadata-json
- --thermal-tiff-dir
- --output-dir

可选参数包括：

- --ambient-temperature-celsius
- --relative-humidity-percent
- --emissivity-ratio
- --distance-to-target-m
- --reflected-temperature-celsius
- --atmospheric-pressure-hpa
- --environment-source
- --environment-source-ref
- --processing-version
- --input-is-temperature
- --raw-to-temperature-scale
- --raw-to-temperature-offset
- --max-frames

注意：虽然代码对部分环境参数提供了默认值，但从 radiometry_model.md 的设计要求看，这些参数在正式运行时应尽量显式提供，否则温度结果会降级，质量标记也可能变差。

## 3. 运行前应该做的操作

### 3.1 准备 Python 环境

先激活项目的虚拟环境，确认依赖可用。当前代码依赖至少包括：

- opencv-python
- numpy
- scipy
- pandas
- torch
- tifffile
- Pillow
- PyYAML

如果要跑 Metashape 相关流程，还要确认 Agisoft Metashape Professional 2.2.1 的 Python API 已配置好。

### 3.2 检查目录结构

先确认以下路径存在且内容正确：

- test_Arctic/rgb_dir：可见光图像
- test_Arctic/tiff_dir：热红外 TIFF 主输入帧
- test_Arctic/thermal_dir：热红外 JPG 预览图，只用于检查，不作为生产输入
- M400-H30T-CALIB-CHESSBOARD/RGB：RGB 标定图
- M400-H30T-CALIB-CHESSBOARD/NIR：热红外标定图
- TWMM-main：TWMM 代码目录
- metadata_all.json：热辐射元数据文件

### 3.3 确认输入类型

热红外生产链路必须使用 TIFF 主输入帧，不要把 thermal_dir 里的 JPG 预览图当作正式输入。JPG 只适合人工预览、对照和排查。

### 3.4 准备环境参数

如果要做准确的辐射校正，建议提前准备以下字段：

- 环境温度
- 相对湿度
- 材料发射率
- 传感器到目标距离

有条件的话再补充：

- 反射温度
- 大气压

### 3.5 确认输出目录可写

所有阶段都会把结果写入 runs 目录下的运行子目录。运行前要确认工作区有写权限，且现有 run_id 不会冲突。

## 4. 推荐运行顺序

如果你想逐步排查，建议按下面顺序执行：

1. 先跑 matching 或 radiometry，确认输入路径和依赖没问题
2. 再跑 run_all，验证全链路编排
3. 最后检查 runs 目录里的标定、去畸变、匹配、辐射、重建、重投影和富集产物

## 5. 常见现象

- 直接运行 python main.py：会提示缺少 command
- matching 少任何必填参数：会在 argparse 阶段直接退出
- radiometry 少任何必填参数：会在 argparse 阶段直接退出
- run_all 能启动但后续报文件不存在：通常是数据目录、标定目录或元数据文件没准备好

## 6. 建议的下一步

如果你想降低再次出错的概率，建议先做两件事：

- 先手动检查 test_Arctic、M400-H30T-CALIB-CHESSBOARD、TWMM-main 和 metadata_all.json 是否都在
- 再用 matching 或 radiometry 的完整命令跑一次小范围验证，然后再切到 run_all
