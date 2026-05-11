import sys
import numpy as np
import cv2
import os
from thermal_visible import ThermalVisble
from image_process import filter_outliers, findHomoraphy
from tools import csv_to_list, filter_list
import random
import time

multiprocess_flag = True

def one_img_process(therma_img_path, therma_rgb_path, visible_img_path, out_img, kwargs):
    thermal_visible_c = ThermalVisble(
        thermal_tiff_path=therma_img_path,
        thermal_rgb_path=therma_rgb_path,
        visible_rgb_path=visible_img_path,
        scale=kwargs['thermal_upsample'],
        crop_size=kwargs['crop_size'],
        attention_flag=False
    )

    s1 = time.time()
    # 提取CFOG特征
    thermal_visible_c.get_img_features(method='CFOG', bin_size=9)
    # CFOG TAMM配准
    corres = thermal_visible_c.get_correspoints(method='TAMM',
                                                patch_size=kwargs['patch_size'],
                                                search_radius=kwargs['search_radius'],
                                                level_max=kwargs['level_max'])
    s2 = time.time()
    print(s2 - s1)



    # 根据corres的实际数据类型调整访问方式
    if isinstance(corres, dict):
        # 假设corres是一个字典，我们需要迭代它的键值对
        thermal_list = []
        visible_list = []
        for key, value in corres.items():
            thermal_list.append(key)
            visible_list.append(value[0])  # 假设value是一个元组，且第一个元素是可见图像中的点
    else:
        print("corres is not a dictionary. Skipping further processing.")
        return None

    # filer points
    thermal_point_list, sen_point_list, outliers_ref, outliers_sen = filter_outliers(thermal_list, visible_list, thresh=2, method='NBCS')
    good = [[_thermal[1], _thermal[0], _sen[1], _sen[0]] for (_thermal, _sen) in zip(thermal_point_list, sen_point_list)]
    H_mat, flag_result = findHomoraphy(good)

    # 做warp
    thermal_visible_c.homo_warp(H_mat)
    thermal_visible_c.draw_matchpoints(thermal_point_list, sen_point_list, outliers_ref, outliers_sen)

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

if __name__ == '__main__':
    
    # thermal_path = 'C:/Users/16854/Desktop/TWMM-main/test_img/3a.tiff'
    # thermal_rgb_path = 'C:/Users/16854/Desktop/TWMM-main/test_img/3a.jpg'
    # visible_path = 'C:/Users/16854/Desktop/TWMM-main/test_img/3b.jpg'
    # output_path = 'C:/Users/16854/Desktop/TWMM-main/test_img/'

    #thermal_path = r"C:\Users\16854\Desktop\code\TWMM-main\datasets\test\1a.tiff"
    #thermal_rgb_path = r"C:\Users\16854\Desktop\code\TWMM-main\datasets\test\1a.jpg"
    #visible_path = r"C:\Users\16854\Desktop\code\TWMM-main\datasets\test\1b.jpg"
    #output_path = r"C:\Users\16854\Desktop\code\TWMM-main\datasets\test\output"
    #"D:\OneDrive\Desktop\DJI_0002_T_Rad_TIFF.tif"

    thermal_path = r"D:\OneDrive\Desktop\TWMM-main\datasets\example\DJI_0076_T_Rad_TIFF.tif"
    thermal_rgb_path = r"D:\OneDrive\Desktop\TWMM-main\datasets\example\DJI_0076_T.jpg"
    visible_path = r"D:\OneDrive\Desktop\TWMM-main\datasets\example\DJI_0075_W.jpg"
    output_path = 'D:/OneDrive/Desktop/test_image8'

    kwargs = {
        'thermal_upsample': 3, #热图像上采样因子。 这个参数用于控制热图像的上采样程度。上采样是指增加图像的分辨率，通常是通过插值算法实现的。在这个例子中，1.0意味着不对热图像进行上采样，保持其原始分辨率。
        'crop_size': (256,256), #裁剪大小。 这个参数指定了图像在配准前应该被裁剪到的大小。在这个例子中，(256, 256)意味着图像将被裁剪为256x256像素的大小。裁剪通常用于减少计算量或确保所有图像具有相同的尺寸。
        'patch_size': 32, #补丁大小。在配准过程中，图像通常被分割成多个小补丁（或窗口），然后对这些补丁进行匹配。patch_size参数指定了每个补丁的大小。在这个例子中，32意味着每个补丁将是32x32像素。较大的补丁可能包含更多的信息，但也可能需要更多的计算资源。
        'search_radius':16, #搜索半径。在配准过程中，对于每个热图像补丁，需要在可见图像中搜索对应的补丁。search_radius参数指定了搜索的范围（以像素为单位）。在这个例子中，16意味着将在每个热图像补丁周围16像素的范围内搜索对应的可见图像补丁。较大的搜索半径可能会增加找到正确匹配的机会，但也会增加计算量。
        'level_max': 4, #金字塔层数，最大为5层
        'scale': {'thermal': 1.0, 'visible':0.25} #尺度因子，用于调整图片缩放大小。比如{'thermal': 1.0, 'visible': 2.3}意味着热图像的尺度因子为1.0（即不进行缩放），而可见光图像的尺度因子为2.3（即可见图像相对于热图像被放大了2.3倍）。尺度因子在配准过程中非常重要，因为它们帮助算法调整不同图像之间的物理尺寸差异
    }

    one_img_process(therma_img_path=thermal_path,
                    therma_rgb_path=thermal_rgb_path,
                    visible_img_path=visible_path,
                    out_img=output_path,
                    kwargs=kwargs)