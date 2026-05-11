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
    
    # 计算缩放比例
    scale_ratio = kwargs.get('registration_scale', 0.25)  # 默认下采样到1/4进行配准
    
    thermal_visible_c = ThermalVisble(
        thermal_tiff_path=therma_img_path,
        thermal_rgb_path=therma_rgb_path,
        visible_rgb_path=visible_img_path,
        scale=kwargs.get('scale', {'thermal': 1.0, 'visible': scale_ratio}),
        crop_size=kwargs['crop_size'],
        attention_flag=False
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
    
    # 将单应性矩阵缩放到原始可见光图像尺寸
    if H_mat is not None and scale_ratio != 1.0:
        # 单应性矩阵H将点从热红外图像变换到可见光图像
        # 如果可见光图像下采样了scale_ratio倍，那么H中的坐标需要相应缩放
        # 正确的缩放方法：H_original = S * H_downsampled * S^-1
        # 其中S是缩放矩阵，但这里我们只需要缩放平移部分
        H_mat_scaled = H_mat.copy()
        # 平移部分需要除以scale_ratio（因为目标图像尺寸变大了）
        H_mat_scaled[0:2, 2] = H_mat[0:2, 2] / scale_ratio
        H_mat = H_mat_scaled
        print(f"已将单应性矩阵从配准尺寸缩放到原始可见光尺寸 (缩放比例: {1/scale_ratio:.2f}x)")

    # 做warp（在裁剪后的图像上，用于可视化）
    thermal_visible_c.homo_warp(H_mat)
    thermal_visible_c.draw_matchpoints(thermal_point_list, sen_point_list, outliers_ref, outliers_sen)
    
    # 在原始可见光尺寸上生成配准后的热红外图像
    if kwargs.get('output_full_resolution', True):
        # 读取原始热红外图像
        original_thermal_rgb = cv2.imread(therma_rgb_path)
        if original_thermal_rgb is None:
            print("警告: 无法读取原始热红外JPG图像，跳过全分辨率输出")
        else:
            # 在原始尺寸上应用单应性变换
            warp_thermal_full = cv2.warpPerspective(
                original_thermal_rgb, H_mat,
                (original_visible_w, original_visible_h)
            )
            # 保存全分辨率配准结果
            full_res_output_dir = os.path.join(out_img, 'warp_thermal_full_resolution')
            os.makedirs(full_res_output_dir, exist_ok=True)
            full_res_path = os.path.join(full_res_output_dir, thermal_visible_c.img_name + '.png')
            cv2.imwrite(full_res_path, warp_thermal_full)
            print(f"已保存全分辨率配准结果: {full_res_path} (尺寸: {original_visible_w}×{original_visible_h})")

    # 结果保存
    save_dict = {
        'corres_points': 'csv',
        'homo': 'csv',
        'matchpoints_img': 'jpg',
        'visible_rgb': 'png',
        'thermal_rgb': 'png',
        'warp_thermal': 'png',
        'mosaic': 'png'
    }
    thermal_visible_c.result_save(out_img, save_dict)
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
    visible_dir = r'E:\thermal-res-task\test\visible'
    thermal_jpg_dir = r'E:\thermal-res-task\test\thermal'
    thermal_tif_dir = r'E:\thermal-res-task\test\thermal_TIFF'
    output_dir = r'E:\thermal-res-task\test\output'

    # 配准参数（针对可见光8000×6000，热红外640×512优化）
    # 策略：在下采样后的图像上进行配准（提高速度），然后缩放到原始尺寸
    kwargs = {
        # 图像预处理参数
        'registration_scale': 0.25,  # 可见光下采样到1/4进行配准（8000×6000 -> 2000×1500）
        'scale': {
            # 热红外上采样：640×512 -> 约2000×1600（保持宽高比），然后裁剪到2000×1500
            'thermal': 3.125,  # 640*3.125=2000, 512*3.125=1600
            # 可见光下采样：8000×6000 -> 2000×1500
            'visible': 0.25    # 8000*0.25=2000, 6000*0.25=1500
        },
        'crop_size': (2000, 1500),  # 裁剪尺寸，匹配配准时的图像尺寸（宽度×高度）
        
        # TAMM配准核心参数
        'patch_size': 48,  # 增大patch_size以适应更大的图像（原32，现48）
        'search_radius': 32,  # 增大搜索半径（原16，现32，约为patch_size的2/3）
        'level_max': 4,  # 增加层级数以提高精度（原3，现4）
        
        # 特征提取参数
        'bin_size': 9,  # CFOG特征方向bin数量（默认值，通常不需要调整）
        
        # 异常点过滤参数
        'outlier_thresh': 3,  # 异常点过滤阈值（原2，现3，适应更大图像）
        
        # 输出选项
        'output_full_resolution': True  # 是否输出全分辨率配准结果（8000×6000）
    }
    
    # 文件处理参数
    start_index = 1  # 热红外图像的起始序号（例如：1 对应 DJI_0001_T）
    num_images = None  # 要处理的图像数量，None表示自动检测

    # 批量处理
    batch_img_process(visible_dir, thermal_jpg_dir, thermal_tif_dir, output_dir, kwargs, 
                     start_index=start_index, num_images=num_images)