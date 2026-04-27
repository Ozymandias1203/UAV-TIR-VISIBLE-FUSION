# radiometry_model.md

## 1. 文档目的

本文档定义热红外辐射校正层的设计。该层负责把热红外 TIFF 主输入帧、设备元数据和环境参数转换为可用于后续热富集的温度矩阵，并明确区分原始传感器值、辐射校正中间值和最终温度值。

## 2. 适用范围

- 适用于 DJI H30T 的热红外 TIFF 主处理帧。
- 适用于单帧热红外温度矩阵的生成。
- 适用于需要将环境温度、相对湿度、发射率和距离纳入校正的工作流。
- JPG 热红外预览图仅用于人工检查或展示，不用于温度矩阵生成。
- 不负责几何去畸变、匹配或三维重建。

## 3. 输入来源

### 3.1 原始图像

- 热红外 TIFF 主输入帧
- 图像尺寸与时间戳
- 若存在，则记录原始传感器计数值或相机输出的原始栅格
- 若存在 JPG 预览图，仅作为人工检查材料保留，不进入校正链路

### 3.2 设备元数据

- DJI 官方 SDK 可读取的相机参数
- ExifTool 可提取的附加字段
- 传感器型号和固件版本
- 曝光、增益、测温模式等可用信息

### 3.3 环境参数

- 环境温度
- 相对湿度
- 材料发射率
- 传感器到物体的距离
- 必要时还包括反射温度或背景温度的估计值

### 3.4 元数据映射与优先级

设备元数据与外部环境参数必须分层处理，优先级从高到低为：外部环境输入、设备元数据、推导值。设备元数据只能作为诊断值、初值或来源记录，不能悄悄替代外部环境输入。

| 原始来源 | 内部字段 | 单位 | 说明 |
|---|---|---|---|
| `Make` + `Model` | `sensor_model` | - | 设备型号 |
| `DateTimeOriginal` / `CreateDate` / `SubSec*` | `capture_timestamp` | ISO 8601 UTC | 帧级时间戳 |
| `FocalLength` | `focal_length_mm` | mm | 物理焦距，和 calibration XML 的 `f` 不同 |
| `FNumber` | `f_number` | - | 光圈值 |
| `ISO` | `iso` | - | 感光度 |
| `ExposureTime` | `exposure_time_s` | s | 曝光时间 |
| `SensorTemperature` | `sensor_temperature_celsius` | °C | 诊断值，不得当作环境温度 |
| `LensTemperature` | `lens_temperature_celsius` | °C | 诊断值，不得当作环境温度 |
| `LRFTargetDistance` | `lrf_target_distance_m` | m | 设备侧距离参考，仅作诊断 |
| `LightValue` | `light_value_ev` | EV | 诊断值，可用于质量分析 |
| `ExifToolVersion` | `exiftool_version` | - | 元数据来源版本 |

如果某一字段缺失，必须在输出中显式列出缺失字段，而不是默默填入隐式默认值。

## 4. 输出定义

### 4.1 核心输出

- 温度矩阵
- 温度单位
- 辐射校正参数
- 时间戳
- 质量标记
- 温度矩阵尺寸
- 温度矩阵数据类型

### 4.2 辅助输出

- 原始值摘要
- 中间辐射量摘要
- 缺失字段列表
- 处理版本信息

## 5. 分层处理

### 5.1 元数据采集层

- 从 DJI SDK 和 ExifTool 汇总与温度计算相关的字段。
- 将字段映射到统一的内部数据结构。
- 记录哪些参数来自设备、哪些参数来自外部输入。
- 记录缺失字段、默认值来源和字段版本，不允许静默降级。

### 5.2 原始值整理层

- 读取 TIFF 热红外像素栅格。
- 保留原始传感器值或原始测温输出的引用。
- 计算必要的基础统计，例如最小值、最大值、均值和异常截断比例。

### 5.3 辐射校正层

- 根据设备参数和环境参数进行校正。
- 将原始值或中间辐射量转为最终温度值。
- 处理无效值、饱和区和边界异常。

### 5.4 标准化层

- 统一温度单位。
- 确保输出矩阵与原始图像尺寸对齐，除非明确记录了重采样。
- 生成质量标记和处理摘要。

## 6. 关键设计约束

### 6.1 物理量分离

- 原始传感器值不能与最终温度值混用。
- 中间辐射量不能被直接当作摄氏度输出。
- 每个字段必须带单位或明确的物理含义。

### 6.2 空间一致性

- 温度矩阵必须与对应 TIFF 热红外图像空间对齐。
- 如果发生任何重采样，必须记录重采样方式和尺度变化。
- 该层不执行与 RGB 图像的几何配准。

### 6.3 可追溯性

- 每个温度矩阵都应能追溯到对应原始帧、元数据和参数版本。
- 若某个参数缺失并使用默认值，必须在质量标记中显式体现。

## 7. 推荐字段

### 7.1 元数据结构

- `frame_id`
- `timestamp`
- `sensor_model`
- `software_version`
- `raw_image_path`
- 在本项目中，`raw_image_path` 必须指向 TIFF 主输入帧。
- `ambient_temperature_celsius`
- `relative_humidity_percent`
- `emissivity_ratio`
- `distance_to_target_m`
- `reflected_temperature_celsius`
- `atmospheric_pressure_hpa`
- `radiometric_mode`
- `parameter_source`
- `parameter_source_ref`
- `quality_flag`

### 7.2 输出结构

- `temperature_matrix`
- `temperature_matrix_shape`
- `temperature_matrix_dtype`
- `temperature_unit`
- `raw_value_range`
- `corrected_value_range`
- `intermediate_value_range`
- `metadata_source`
- `parameter_source`
- `missing_fields`

## 8. 质量门槛

- 温度值范围应符合场景常识。
- 输出矩阵中不应出现大量未解释的 NaN 或极端值。
- 不同帧之间的温度分布不应出现明显的参数漂移，除非场景本身确实发生变化。
- 若缺少关键元数据，应输出降级质量标记，而不是默默生成看似正常的结果。
- 如果缺少 `ambient_temperature_celsius`、`relative_humidity_percent`、`emissivity_ratio` 或 `distance_to_target_m`，必须显式标记为 `degraded` 或 `invalid`，并列出缺失字段。

## 9. 与其他层的接口

### 9.1 与 matching 的关系

- radiometry 不参与匹配。
- matching 只消费几何图像，不消费辐射校正结果。

### 9.2 与 enrichment 的关系

- enrichment 仅消费已经生成的温度矩阵。
- enrichment 不应重复做辐射校正。

### 9.3 与文件格式的关系

- 温度矩阵和元数据应分别保存，并在 file_formats.md 中定义一致的字段。
- 只要这里新增字段，file_formats.md 里的持久化 schema 必须同步更新，字段名、单位和可选性必须保持完全一致。

## 10. 失败模式

- 缺少环境参数。
- SDK 或 ExifTool 提取字段不完整。
- 温度值明显超出合理范围。
- 原始图像与元数据时间不一致。
- 设备固件或参数版本变化导致校正结果不可比。

遇到这些情况时，应保留中间结果和错误上下文，便于重新校正。

## 11. 非目标

- 不是几何去畸变模块。
- 不是热红外与 RGB 的空间配准模块。
- 不是三维点云温度赋值模块。