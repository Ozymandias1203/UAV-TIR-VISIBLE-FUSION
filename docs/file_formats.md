# file_formats.md

## 1. 文档目的

本文档定义本项目各阶段的持久化数据格式与字段契约。目标是让所有阶段的输入和输出都能稳定、可审计、可回放，并尽量避免“同一份数据在不同模块里被不同理解”的问题。

## 2. 总体规范

- 所有机器可读文件都应包含 `schema_version`、`source_version`、`created_at` 和 `units`。
- 任何数值型输出都必须明确单位，不允许把像素、毫米、米、摄氏度混写在同一字段中。
- 原始数据和派生数据必须分目录保存，禁止覆盖原始输入。
- 人审友好格式与机器高效格式可以并存，但二者必须引用同一份逻辑标识。
- 坐标系、像素原点和时间基准必须在文件头中声明。

## 3. 通用约定

| 项目 | 约定 |
|---|---|
| 坐标系 | 像素坐标原点位于左上角，x 向右，y 向下；三维坐标默认米制右手系，除非文件头另行说明 |
| 时间 | 统一使用 ISO 8601 UTC |
| 编码 | 文本文件使用 UTF-8 |
| 小数 | 统一使用英文小数点 |
| 图像 | 输入和诊断图以无损格式优先 |
| 版本 | 逻辑 schema 与算法版本分开记录 |

## 4. 核心文件类型

| 逻辑对象 | 推荐格式 | 主要用途 |
|---|---|---|
| 运行清单 | JSON | 记录一次批处理任务的输入、输出和状态 |
| 标定结果 | XML + JSON | 与现有相机参数文件和机器可读摘要兼容 |
| 去畸变图像 | PNG / TIFF | 保留几何校正后的像素内容 |
| 匹配结果 | JSON + CSV + PNG | 记录对应点、外点、单应性和可视化检查图 |
| 温度矩阵 | NPY / NPZ + JSON | 保留完整温度栅格与元数据 |
| 重投影记录 | CSV / JSONL | 便于按点、按图像统计分析 |
| 热富集点云 | PLY / LAS + JSON | 供下游查看和统计 |
| 质检报告 | JSON + PNG / SVG | 记录覆盖率、误差和异常分布 |

## 5. 运行清单

### 5.1 作用

运行清单是一次批处理的总入口和总出口，负责串起所有阶段的输入路径、输出路径、参数版本和执行状态。

### 5.2 必要字段

- `run_id`
- `dataset_id`
- `created_at`
- `operator`
- `pipeline_version`
- `input_paths`
- `output_paths`
- `stage_status`
- `stage_versions`
- `notes`

### 5.3 约束

- 必须能复现一次运行用了什么输入和参数。
- 不得只记录相对路径而不保留项目根上下文。
- 每个阶段结束后都应更新状态和时间戳。

## 6. 标定结果

### 6.1 文件角色

标定结果用于保存 H30T RGB / NIR 的相机内参、畸变参数、重投影误差和质量标记。它既要兼容当前 XML 参考文件，也要有 JSON 摘要方便后续模块读取。

### 6.2 必要字段

- `sensor_name`
- `image_width`
- `image_height`
- `focal_length`
- `principal_point`
- `affinity_or_skew`
- `distortion_coefficients`
- `reprojection_rms`
- `chessboard_detection_rate`
- `quality_flag`
- `source_images`

### 6.3 约束

- 主参数必须对应到实际图像尺寸。
- 主点偏移必须注明是相对图像中心还是绝对像素坐标。
- 若结果来自多轮迭代，必须记录最终采用的是哪一轮。

## 7. 去畸变图像

### 7.1 文件角色

去畸变图像是后续匹配和几何投影的统一输入。它应尽量保持像素内容可读，但不再携带原始畸变几何。

### 7.2 必要字段

- `source_image`
- `undistortion_model`
- `cropped`
- `resampled`
- `scale_factor`
- `calibration_ref`
- `output_size`

### 7.3 约束

- 必须记录是否发生裁切或缩放。
- 必须记录与原图的映射关系。
- 不能把去畸变图当作原始辐射数据的替代品。

## 8. TWMM 匹配结果

### 8.1 文件角色

匹配结果记录去畸变 RGB / 热红外图像对的对应点集合、外点剔除结果和单应性矩阵，是热富集的关键几何桥梁。

### 8.2 必要字段

- `pair_id`
- `source_rgb`
- `source_thermal`
- `correspondences`
- `inlier_mask`
- `homography_matrix`
- `confidence`
- `match_quality`
- `runtime_ms`
- `algorithm_version`

### 8.3 对应点记录

每个对应点至少应包含：

- RGB 图像坐标
- 热红外图像坐标
- 是否为内点
- 局部匹配置信息
- 参与的模板尺度或层级信息

### 8.4 约束

- 对应点必须能追溯到原始图像对。
- 单应性矩阵必须和对应点集合一起保存。
- 诊断图应与数值结果绑定保存。

## 9. 温度矩阵

### 9.1 文件角色

温度矩阵保存单帧热红外图像的最终温度栅格，是热富集的唯一温度来源。

### 9.2 必要字段

- `frame_id`
- `temperature_matrix`
- `unit`
- `raw_sensor_reference`
- `radiometric_parameters`
- `environment_parameters`
- `timestamp`
- `quality_flag`

### 9.3 约束

- 必须区分原始传感器值、辐射校正中间值和最终温度值。
- 输出必须明确单位，默认摄氏度。
- 温度矩阵尺寸必须与对应热红外图像一致，除非另行记录重采样。

## 10. 重投影记录

### 10.1 文件角色

重投影记录描述三维点在各张 RGB 图像中的投影位置、误差与可见性，是几何证据层的输出。

### 10.2 必要字段

- `point_id`
- `image_id`
- `projected_x`
- `projected_y`
- `reprojection_error_px`
- `visibility_state`
- `occlusion_state`
- `support_rank`

### 10.3 约束

- 必须保留点到图像的多对多关系。
- 可见性状态必须能区分“未观测”“可见”“遮挡”“边界外”等情况。
- 不应把温度值混入几何记录中。

## 11. 热富集点云

### 11.1 文件角色

热富集点云是在 RGB 几何点云上附加温度属性后的最终产品。

### 11.2 必要字段

- `point_id`
- `x`
- `y`
- `z`
- `r`
- `g`
- `b`
- `temperature`
- `temperature_unit`
- `support_view_count`
- `fusion_weight`
- `quality_score`

### 11.3 约束

- 每个点的温度必须能追溯到一个或多个温度矩阵样本。
- 必须记录该点的支持视角数和融合方式。
- 若点温度缺失，必须显式标记，而不是默认填零。

## 12. 质检报告

### 12.1 文件角色

质检报告用于人工快速检查算法是否在正确轨道上，特别是用于跨会话回归判断。

### 12.2 推荐内容

- 标定识别率和重投影误差分布
- 匹配外点比例和单应性稳定性
- 温度矩阵范围和异常值统计
- 点云覆盖率和温度覆盖率
- 典型叠加图和错误案例截图

## 13. 命名与目录建议

建议使用按阶段分层的目录结构，例如：

- `runs/`：运行清单和总日志
- `calibration/`：标定结果
- `preprocess/`：去畸变图像和预处理产物
- `matching/`：对应点、单应性和可视化
- `radiometry/`：温度矩阵和辐射参数
- `reconstruction/`：Metashape 项目和点云
- `enrichment/`：热富集结果
- `reports/`：质检材料

## 14. 非目标

- 不是规定某个单一序列化库必须使用。
- 不是强制所有阶段都使用同一种文件格式。
- 不是把临时调试输出混入正式数据契约。