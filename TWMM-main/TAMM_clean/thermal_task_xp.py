import os
import numpy as np
import cv2
from thermal_visible import ThermalVisble
from image_process import filter_outliers, findHomoraphy
import time

def one_img_process(therma_img_path, therma_rgb_path, visible_img_path, out_img, kwargs):
    # 读取原始图像尺寸（用于后续缩放单应性矩阵）
    original_visible = cv2.imread(visible_img_path)
    if original_visible is None:
        raise RuntimeError(f"无法读取可见光图像: {visible_img_path}")
    original_visible_h, original_visible_w = original_visible.shape[:2]
    
    # 读取原始热红外图像尺寸（用于确定配准尺寸）
    original_thermal_rgb = cv2.imread(therma_rgb_path)
    if original_thermal_rgb is None:
        raise RuntimeError(f"无法读取热红外图像: {therma_rgb_path}")
    original_thermal_h, original_thermal_w = original_thermal_rgb.shape[:2]
    
    # 计算可见光下采样比例，使其接近热红外尺寸
    # 策略：下采样可见光到稍大于热红外的尺寸，然后裁剪到匹配尺寸
    scale_ratio = kwargs.get('registration_scale', None)
    if scale_ratio is None:
        # 自动计算：使可见光下采样后宽度接近热红外宽度
        # 例如：8000×6000 -> 约800×600（scale=0.1），然后裁剪到640×512
        scale_factor = kwargs.get('scale_factor', 1.25)
        scale_ratio = original_thermal_w / original_visible_w * scale_factor
        # scale_factor > 1 确保下采样后稍大于热红外，便于裁剪对齐
        print(f"[调试信息] 自动计算下采样比例:")
        print(f"  原始可见光尺寸: {original_visible_w}×{original_visible_h}")
        print(f"  原始热红外尺寸: {original_thermal_w}×{original_thermal_h}")
        print(f"  计算的下采样比例: {scale_ratio:.4f}")
        print(f"  下采样后可见光尺寸: {int(original_visible_w * scale_ratio)}×{int(original_visible_h * scale_ratio)}")
    else:
        # 手动指定
        print(f"[调试信息] 使用手动指定的下采样比例: {scale_ratio:.4f}")
        print(f"  原始可见光尺寸: {original_visible_w}×{original_visible_h}")
        print(f"  下采样后可见光尺寸: {int(original_visible_w * scale_ratio)}×{int(original_visible_h * scale_ratio)}")
        print(f"  原始热红外尺寸: {original_thermal_w}×{original_thermal_h}")
        print(f"  裁剪尺寸: {kwargs['crop_size']}")
    
    # 获取裁剪偏移参数
    crop_offset = kwargs.get('crop_offset', (0, 0))  # (offset_x, offset_y)，默认无偏移
    if crop_offset != (0, 0):
        print(f"[调试信息] 裁剪区域偏移: offset_x={crop_offset[0]}, offset_y={crop_offset[1]}")
        if crop_offset[0] > 0:
            print(f"  水平方向: 向左移动 {crop_offset[0]} 像素")
        elif crop_offset[0] < 0:
            print(f"  水平方向: 向右移动 {abs(crop_offset[0])} 像素")
        if crop_offset[1] > 0:
            print(f"  垂直方向: 向上移动 {crop_offset[1]} 像素")
        elif crop_offset[1] < 0:
            print(f"  垂直方向: 向下移动 {abs(crop_offset[1])} 像素")
    
    thermal_visible_c = ThermalVisble(
        thermal_tiff_path=therma_img_path,
        thermal_rgb_path=therma_rgb_path,
        visible_rgb_path=visible_img_path,
        scale={'thermal': 1.0, 'visible': scale_ratio},  # 热红外不上采样，只下采样可见光
        crop_size=kwargs['crop_size'],
        attention_flag=False,
        crop_offset=crop_offset  # 裁剪区域偏移量
    )

    s1 = time.time()
    # 提取CFOG特征
    thermal_visible_c.get_img_features(method='CFOG', bin_size=kwargs.get('bin_size', 9))
    # CFOG TAMM配准
    corres = thermal_visible_c.get_correspoints(method='TAMM',
                                                patch_size=kwargs['patch_size'],
                                                search_radius=kwargs['search_radius'],
                                                level_max=kwargs['level_max'])
    s2 = time.time()
    print(f"配准时间: {s2 - s1} 秒")

    # 根据corres的实际数据类型调整访问方式
    if isinstance(corres, dict):
        thermal_list = []
        visible_list = []
        for key, value in corres.items():
            thermal_list.append(key)
            visible_list.append(value[0])  # 假设value是一个元组，且第一个元素是可见图像中的点
    else:
        print("corres 不是字典类型，跳过后续处理。")
        return None

    # 过滤异常点
    thermal_point_list, sen_point_list, outliers_ref, outliers_sen = filter_outliers(
        thermal_list, visible_list, 
        thresh=kwargs.get('outlier_thresh', 2), 
        method='NBCS'
    )
    good = [[_thermal[1], _thermal[0], _sen[1], _sen[0]] for (_thermal, _sen) in zip(thermal_point_list, sen_point_list)]
    H_mat, flag_result = findHomoraphy(good)
    
    # 保存用于裁剪后图像的单应性矩阵（用于可视化）
    H_mat_for_crop = H_mat.copy()
    
    # 将单应性矩阵缩放到原始可见光图像尺寸（用于最终输出）
    H_mat_scaled = None
    if H_mat is not None and scale_ratio != 1.0:
        # 单应性矩阵H将点从热红外图像变换到可见光图像
        # 如果可见光图像下采样了scale_ratio倍，那么H中的坐标需要相应缩放
        # 正确的缩放方法：H_original = S * H_downsampled * S^-1
        # 其中S是缩放矩阵，但这里我们只需要缩放平移部分
        H_mat_scaled = H_mat.copy()
        # 平移部分需要除以scale_ratio（因为目标图像尺寸变大了）
        H_mat_scaled[0:2, 2] = H_mat[0:2, 2] / scale_ratio
        print(f"已将单应性矩阵从配准尺寸缩放到原始可见光尺寸 (缩放比例: {1/scale_ratio:.2f}x)")

    # 做warp（在裁剪后的图像上，用于可视化）- 使用未缩放的单应性矩阵
    thermal_visible_c.homo_warp(H_mat_for_crop)
    thermal_visible_c.draw_matchpoints(thermal_point_list, sen_point_list, outliers_ref, outliers_sen)
    
    # 更新单应性矩阵为缩放后的版本（用于后续保存和输出）
    if H_mat_scaled is not None:
        thermal_visible_c.homo = H_mat_scaled
        H_mat = H_mat_scaled
    
    # 输出变换后的热红外图像（保持原始分辨率640×512）
    if kwargs.get('output_warped_thermal', True):
        # 使用之前读取的原始热红外图像
        if original_thermal_rgb is None:
            print("警告: 无法读取原始热红外JPG图像，跳过输出")
        else:
            
            # 先将热红外图像变换到可见光坐标系（全尺寸）
            warp_thermal_full = cv2.warpPerspective(
                original_thermal_rgb, H_mat,
                (original_visible_w, original_visible_h)
            )
            
            # 计算热红外图像四个角点在可见光坐标系中的位置
            thermal_corners = np.array([
                [0, 0],  # 左上
                [original_thermal_w, 0],  # 右上
                [original_thermal_w, original_thermal_h],  # 右下
                [0, original_thermal_h]  # 左下
            ], dtype=np.float32).reshape(-1, 1, 2)
            
            # 将角点变换到可见光坐标系
            visible_corners = cv2.perspectiveTransform(thermal_corners, H_mat).reshape(-1, 2)
            
            # 计算变换后区域的边界框（在可见光坐标系中）
            min_x = max(0, int(np.floor(visible_corners[:, 0].min())))
            min_y = max(0, int(np.floor(visible_corners[:, 1].min())))
            max_x = min(original_visible_w, int(np.ceil(visible_corners[:, 0].max())))
            max_y = min(original_visible_h, int(np.ceil(visible_corners[:, 1].max())))
            
            # 从变换后的图像中裁剪出对应区域
            crop_region = warp_thermal_full[min_y:max_y, min_x:max_x]
            
            # Resize到原始热红外分辨率（640×512）
            warped_thermal = cv2.resize(crop_region, (original_thermal_w, original_thermal_h), 
                                      interpolation=cv2.INTER_LINEAR)
            
            # 保存变换后的热红外图像（保持原始分辨率640×512）
            warped_output_dir = os.path.join(out_img, 'warped_thermal_original_resolution')
            os.makedirs(warped_output_dir, exist_ok=True)
            warped_path = os.path.join(warped_output_dir, thermal_visible_c.img_name + '.png')
            cv2.imwrite(warped_path, warped_thermal)
            print(f"已保存变换后的热红外图像: {warped_path} (尺寸: {original_thermal_w}×{original_thermal_h}, 已对齐到可见光坐标系)")

    # 结果保存
    try:
        save_dict = {
            'corres_points': 'csv',
            'homo': 'csv',
            'matchpoints_img': 'jpg',
            'visible_rgb': 'png',
            'thermal_rgb': 'png',
            'warp_thermal': 'png',
            'mosaic': 'png'
        }
        print(f"开始保存结果到: {out_img}")
        thermal_visible_c.result_save(out_img, save_dict)
        print("结果保存完成")
    except Exception as e:
        print(f"保存结果时出错: {e}")
        import traceback
        traceback.print_exc()
    
    return H_mat

def batch_img_process(visible_dir, thermal_jpg_dir, thermal_tif_dir, output_dir, kwargs, start_index=1, num_images=None):
    """
    批量处理图像对
    
    参数:
        visible_dir: 可见光图像目录
        thermal_jpg_dir: 热红外JPG图像目录
        thermal_tif_dir: 热红外TIF图像目录
        output_dir: 输出目录
        kwargs: 配准参数
        start_index: 热红外图像的起始序号（默认1，对应DJI_0001_T）
        num_images: 要处理的图像数量，如果为None则自动检测
    """
    # 如果未指定数量，自动检测目录中的文件数量
    if num_images is None:
        # 获取热红外目录中的所有TIF文件
        thermal_files = [f for f in os.listdir(thermal_tif_dir) if f.endswith('.TIF') or f.endswith('.tif')]
        if thermal_files:
            # 从文件名中提取最大序号
            indices = []
            for f in thermal_files:
                try:
                    # 提取DJI_xxxx_T.TIF中的序号
                    if 'DJI_' in f and '_T.' in f:
                        idx_str = f.split('DJI_')[1].split('_T.')[0]
                        indices.append(int(idx_str))
                except:
                    continue
            if indices:
                num_images = max(indices) - start_index + 1
            else:
                num_images = len(thermal_files)
        else:
            print("警告: 无法自动检测图像数量，使用默认值19")
            num_images = 19
    
    # 确保输出目录存在
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    
    # 遍历所有图像对
    processed_count = 0
    for i in range(num_images):
        thermal_index = start_index + i
        visible_index = thermal_index + 1  # 可见光序号比热红外大1
        
        # 生成文件路径（支持大小写）
        thermal_jpg_name = f"DJI_{thermal_index:04d}_T.JPG"
        thermal_tif_name = f"DJI_{thermal_index:04d}_T.TIF"
        visible_name = f"DJI_{visible_index:04d}_W.JPG"
        
        thermal_jpg_path = os.path.join(thermal_jpg_dir, thermal_jpg_name)
        thermal_tif_path = os.path.join(thermal_tif_dir, thermal_tif_name)
        visible_path = os.path.join(visible_dir, visible_name)
        
        # 如果大写文件名不存在，尝试小写
        if not os.path.exists(thermal_jpg_path):
            thermal_jpg_path = os.path.join(thermal_jpg_dir, thermal_jpg_name.lower())
        if not os.path.exists(thermal_tif_path):
            thermal_tif_path = os.path.join(thermal_tif_dir, thermal_tif_name.lower())
        if not os.path.exists(visible_path):
            visible_path = os.path.join(visible_dir, visible_name.lower())
        
        # 检查文件是否存在
        if not os.path.exists(visible_path) or not os.path.exists(thermal_jpg_path) or not os.path.exists(thermal_tif_path):
            print(f"文件不存在，跳过处理:")
            print(f"  可见光: {visible_path}")
            print(f"  热红外JPG: {thermal_jpg_path}")
            print(f"  热红外TIF: {thermal_tif_path}")
            continue
        
        processed_count += 1
        print(f"正在处理图像对 {processed_count} (热红外序号: {thermal_index:04d}, 可见光序号: {visible_index:04d})")
        print(f"  可见光: {visible_path}")
        print(f"  热红外JPG: {thermal_jpg_path}")
        print(f"  热红外TIF: {thermal_tif_path}")
        
        out_img = output_dir
        one_img_process(therma_img_path=thermal_tif_path,
                        therma_rgb_path=thermal_jpg_path,
                        visible_img_path=visible_path,
                        out_img=out_img,
                        kwargs=kwargs)
    
    print(f"\n批量处理完成，共处理 {processed_count} 对图像")

if __name__ == '__main__':
    # 文件夹路径
    #visible_dir = r'E:\thermal-res-task\20240907_temp_M2ET_thermal\visible'
    #thermal_jpg_dir = r'E:\thermal-res-task\20240907_temp_M2ET_thermal\thermal'
    #thermal_tif_dir = r'E:\thermal-res-task\20240907_temp_M2ET_thermal\thermal_TIFF'  
    #output_dir = r'E:\thermal-res-task\20240907_temp_M2ET_thermal\output'
    therma_img_path=r"E:\Campus_Calib_Dataset\UAV\DJI_0074_T_ST_TIFF.tif"
    #"D:\OneDrive\Desktop\TWMM-main\datasets\example\DJI_0130_T_ST_TIFF.tif"
    therma_rgb_path=r"E:\Campus_Calib_Dataset\UAV\DJI_0074_T.JPG"
    #"D:\OneDrive\Desktop\TWMM-main\datasets\example\DJI_0130_T.jpg"
    visible_img_path=r"E:\Campus_Calib_Dataset\UAV\DJI_0073_W.JPG"
    #"D:\OneDrive\Desktop\TWMM-main\datasets\example\DJI_0129_W.jpg"
    out_img=r"E:\Campus_Calib_Dataset\03_TWMM_Result\DJI_0073_W"




    # 配准参数（针对可见光8000×6000，热红外640×512优化）
    # 策略：仅下采样可见光图像，热红外保持原始分辨率，进行匹配
    kwargs = {
        # ========== 图像预处理参数 ==========
        # 下采样比例设置（重要！）
        'registration_scale': None,  # None表示自动计算，或手动指定下采样比例（0.0-1.0之间）
        # 
        # 【手动调试方法】：
        # 1. 设置为 None：自动计算（推荐首次使用）
        #    计算公式：scale_ratio = 热红外宽度 / 可见光宽度 × scale_factor
        #    例如：640/8000 × 1.25 ≈ 0.1
        #
        # 2. 手动指定具体值（推荐调试时使用）：
        #    - 0.08: 下采样到约640×480（接近热红外尺寸，速度快但可能丢失细节）
        #    - 0.10: 下采样到约800×600（推荐起始值，平衡速度和精度）
        #    - 0.12: 下采样到约960×720（保留更多细节，但计算量增加）
        #    - 0.15: 下采样到约1200×900（高精度，但计算较慢）
        #    - 0.20: 下采样到约1600×1200（很高精度，但计算很慢）
        #
        # 【调试建议】：
        # - 如果配准失败或匹配点太少：尝试增大（如0.12, 0.15）
        # - 如果计算太慢：尝试减小（如0.08）
        # - 如果精度不够：逐步增大（0.10 → 0.12 → 0.15）
        # - 如果速度优先：逐步减小（0.10 → 0.08）
        #
        'scale_factor': 1.55,  # 下采样后可见光尺寸相对于热红外的倍数（仅在registration_scale=None时使用）
        # 调整建议：
        # - 1.0-1.2: 下采样后接近热红外尺寸（可能裁剪时信息不足）
        # - 1.25-1.5: 推荐范围（下采样后稍大于热红外，便于裁剪对齐）
        # - 1.5-2.0: 下采样后明显大于热红外（保留更多信息但计算量增加）
        
        'crop_size': (640, 512),  # 裁剪尺寸，匹配热红外原始分辨率（宽度×高度）
        # 注意：裁剪尺寸应该与热红外图像尺寸一致或接近
        
        'crop_offset': (175,115),  # 裁剪区域偏移量 (offset_x, offset_y)
        # offset_x: 水平偏移（>0向左移动，<0向右移动）
        # offset_y: 垂直偏移（>0向上移动，<0向下移动）
        # 示例：
        #   (50, 0): 向左移动50像素（推荐：向左移动）
        #   (0, 30): 向上移动30像素
        #   (-20, 0): 向右移动20像素
        #   (50, 30): 向左移动50像素，向上移动30像素
        #   (0, 0): 无偏移（默认右下角裁剪）
        
        # TAMM配准核心参数（针对640×512尺寸优化）
        'patch_size': 64,  # patch大小，对于640×512图像，32是合适的
        # 调整建议：
        # - 如果匹配点太少：增大到48或64
        # - 如果计算太慢：减小到24或16
        # - 通常设为图像宽度的1/20到1/10
        
        'search_radius': 64,  # 搜索半径，通常设为patch_size的0.5-1倍
        # 调整建议：
        # - 如果位移较大：增大到24或32
        # - 如果位移较小：减小到8或12
        # - 通常设为patch_size的0.5-1倍
        
        'level_max': 4,  # 多层级匹配的最大层级数
        # 调整建议：
        # - 对于640×512图像，3层通常足够
        # - 如果精度不够：增加到4
        # - 如果速度太慢：减少到2
        
        # 特征提取参数
        'bin_size': 12,  # CFOG特征方向bin数量（默认值，通常不需要调整）
        # 可选值：6, 8, 9, 12, 16（越大越精细，但计算量也越大）
        
        # 异常点过滤参数
        'outlier_thresh': 2,  # 异常点过滤阈值（像素误差）
        # 调整建议：
        # - 如果错误匹配多：减小到1或1.5
        # - 如果有效点被过滤：增大到3或4
        # - 对于640×512图像，2通常是合适的
        
        # 输出选项
        'output_warped_thermal': True  # 是否输出变换后的热红外图像（保持原始分辨率640×512）
    }
    
    # 参数设置说明：
    # 1. registration_scale: 可见光下采样比例
    #    - None: 自动计算（推荐），根据热红外尺寸自动确定
    #    - 手动指定: 如0.1表示下采样到1/10（8000×6000 -> 800×600）
    #    - 建议范围: 0.08-0.15（下采样后宽度在640-1200之间）
    #
    # 2. crop_size: 裁剪尺寸，应该与热红外图像尺寸匹配
    #    - 对于640×512热红外，建议使用(640, 512)或稍大一些如(800, 640)
    #    - 裁剪尺寸应该小于等于下采样后的可见光尺寸
    #e
    # 3. patch_size和search_radius: 需要根据图像尺寸和位移范围调整
    #    - 图像尺寸大：增大patch_size
    #    - 位移大：增大search_radius
    #    - 通常patch_size = 图像宽度/20，search_radius = patch_size/2

    # 文件处理参数
    #start_index = 163  # 热红外图像的起始序号（例如：1 对应 DJI_0001_T）
    #num_images = None  # 要处理的图像数量，None表示自动检测

    # 批量处理
    #batch_img_process(visible_dir, thermal_jpg_dir, thermal_tif_dir, output_dir, kwargs, 
    #                start_index=start_index, num_images=num_images)

    # 4. 执行单张配准
    print("开始单张图像配准...")
    H_mat = one_img_process(
        therma_img_path=therma_img_path,
        therma_rgb_path=therma_rgb_path,
        visible_img_path=visible_img_path,
        out_img=out_img,
        kwargs=kwargs
    )
    print(f"单张配准完成！单应性矩阵：\n{H_mat}")