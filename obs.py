import argparse
import os
import random
import shutil
import time
import cv2
import numpy as np
import onnxruntime as ort

import threading
import datetime

global glo


class Detector():

    def __init__(self, onnx_model, imgsz=(640, 640), infer_tool='openvino'):

        self.infer_tool = infer_tool

        # 构建onnxruntime推理引擎
        self.ort_session = ort.InferenceSession(onnx_model,
                                                providers=['CUDAExecutionProvider', 'CPUExecutionProvider']
                                                if ort.get_device() == 'GPU' else ['CPUExecutionProvider'])

        # Numpy dtype: support both FP32 and FP16 onnx model
        self.ndtype = np.half if self.ort_session.get_inputs()[0].type == 'tensor(float16)' else np.single

        self.model_height, self.model_width = imgsz[0], imgsz[1]  # 图像resize大小
        # self.color_palette = np.random.uniform(0, 255, size=(len(self.classes), 3))  # 为每个类别生成调色板

    def __call__(self, im0, conf_threshold=0.7, iou_threshold=0.7):
        """
        The whole pipeline: pre-process -> inference -> post-process.

        Args:
            im0 (Numpy.ndarray): original input image.
            conf_threshold (float): confidence threshold for filtering predictions.
            iou_threshold (float): iou threshold for NMS.

        Returns:
            boxes (List): list of bounding boxes.
        """
        # 前处理Pre-process
        t1 = time.time()
        im, ratio, (pad_w, pad_h) = self.preprocess(im0)
        # print('预处理时间：{:.3f}s'.format(time.time() - t1))

        # 推理 inference
        t2 = time.time()
        if self.infer_tool == 'openvino':
            preds = self.openvino.predict(im)
        else:
            preds = self.ort_session.run(None, {self.ort_session.get_inputs()[0].name: im})[0]
        # print('推理时间：{:.2f}s'.format(time.time() - t2))

        # 后处理Post-process
        t3 = time.time()
        boxes = self.postprocess(preds,
                                 im0=im0,
                                 ratio=ratio,
                                 pad_w=pad_w,
                                 pad_h=pad_h,
                                 conf_threshold=conf_threshold,
                                 iou_threshold=iou_threshold,
                                 )
        # print('后处理时间：{:.3f}s'.format(time.time() - t3))

        return boxes

    # 前处理，包括：resize, pad, HWC to CHW，BGR to RGB，归一化，增加维度CHW -> BCHW
    def preprocess(self, img):
        """
        Pre-processes the input image.

        Args:
            img (Numpy.ndarray): image about to be processed.

        Returns:
            img_process (Numpy.ndarray): image preprocessed for inference.
            ratio (tuple): width, height ratios in letterbox.
            pad_w (float): width padding in letterbox.
            pad_h (float): height padding in letterbox.
        """
        # Resize and pad input image using letterbox() (Borrowed from Ultralytics)
        shape = img.shape[:2]  # original image shape
        new_shape = (self.model_height, self.model_width)
        r = min(new_shape[0] / shape[0], new_shape[1] / shape[1])
        ratio = r, r
        new_unpad = int(round(shape[1] * r)), int(round(shape[0] * r))
        pad_w, pad_h = (new_shape[1] - new_unpad[0]) / 2, (new_shape[0] - new_unpad[1]) / 2  # wh padding
        if shape[::-1] != new_unpad:  # resize
            img = cv2.resize(img, new_unpad, interpolation=cv2.INTER_LINEAR)
        top, bottom = int(round(pad_h - 0.1)), int(round(pad_h + 0.1))
        left, right = int(round(pad_w - 0.1)), int(round(pad_w + 0.1))
        img = cv2.copyMakeBorder(img, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(114, 114, 114))  # 填充

        # Transforms: HWC to CHW -> BGR to RGB -> div(255) -> contiguous -> add axis(optional)
        img = np.ascontiguousarray(np.einsum('HWC->CHW', img)[::-1], dtype=self.ndtype) / 255.0
        img_process = img[None] if len(img.shape) == 3 else img
        return img_process, ratio, (pad_w, pad_h)

    # 后处理，包括：阈值过滤与NMS
    def postprocess(self, preds, im0, ratio, pad_w, pad_h, conf_threshold, iou_threshold):
        """
        Post-process the prediction.

        Args:
            preds (Numpy.ndarray): predictions come from ort.session.run().
            im0 (Numpy.ndarray): [h, w, c] original input image.
            ratio (tuple): width, height ratios in letterbox.
            pad_w (float): width padding in letterbox.
            pad_h (float): height padding in letterbox.
            conf_threshold (float): conf threshold.
            iou_threshold (float): iou threshold.

        Returns:
            boxes (List): list of bounding boxes.
        """
        x = preds  # outputs: predictions (1, 84, 8400)
        # Transpose the first output: (Batch_size, xywh_conf_cls, Num_anchors) -> (Batch_size, Num_anchors, xywh_conf_cls)
        x = np.einsum('bcn->bnc', x)  # (1, 8400, 84)

        # Predictions filtering by conf-threshold
        x = x[np.amax(x[..., 4:], axis=-1) > conf_threshold]

        # Create a new matrix which merge these(box, score, cls) into one
        # For more details about `numpy.c_()`: https://numpy.org/doc/1.26/reference/generated/numpy.c_.html
        x = np.c_[x[..., :4], np.amax(x[..., 4:], axis=-1), np.argmax(x[..., 4:], axis=-1)]

        # NMS filtering
        # 经过NMS后的值, np.array([[x, y, w, h, conf, cls], ...]), shape=(-1, 4 + 1 + 1)
        x = x[cv2.dnn.NMSBoxes(x[:, :4], x[:, 4], conf_threshold, iou_threshold)]

        # 重新缩放边界框，为画图做准备
        if len(x) > 0:
            # Bounding boxes format change: cxcywh -> xyxy
            x[..., [0, 1]] -= x[..., [2, 3]] / 2
            x[..., [2, 3]] += x[..., [0, 1]]

            # Rescales bounding boxes from model shape(model_height, model_width) to the shape of original image
            x[..., :4] -= [pad_w, pad_h, pad_w, pad_h]
            x[..., :4] /= min(ratio)

            # Bounding boxes boundary clamp
            x[..., [0, 2]] = x[:, [0, 2]].clip(0, im0.shape[1])
            x[..., [1, 3]] = x[:, [1, 3]].clip(0, im0.shape[0])

            return x[..., :6]  # boxes
        else:
            return []

    # 绘框
    def draw_and_visualize(self, im, bboxes, vis=False, save=True):
        """
        Draw and visualize results.

        Args:
            im (np.ndarray): original image, shape [h, w, c].
            bboxes (numpy.ndarray): [n, 6], n is number of bboxes.
            vis (bool): imshow using OpenCV.
            save (bool): save image annotated.

        Returns:
            None
        """
        # Draw rectangles
        for (*box, conf, cls_) in bboxes:
            # draw bbox rectangle
            cv2.rectangle(im, (int(box[0]), int(box[1])), (int(box[2]), int(box[3])),
                          self.color_palette[int(cls_)], 1, cv2.LINE_AA)
            cv2.putText(im, f'{self.classes[int(cls_)]}: {conf:.3f}', (int(box[0]), int(box[1] - 9)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, self.color_palette[int(cls_)], 2, cv2.LINE_AA)

        # Show image
        if vis:
            cv2.imshow('demo', im)
            cv2.waitKey(0)
            cv2.destroyAllWindows()

        # Save image
        if save:
            cv2.imwrite('demo.jpg', im)


def split_list(lst, n):
    split_size = len(lst) // n
    remainder = len(lst) % n
    result = []
    start = 0

    for i in range(n):
        if i < remainder:
            end = start + split_size + 1
        else:
            end = start + split_size
        result.append(lst[start:end])
        start = end

    return result


def find_file(root):
    file_list = []
    for rt, dir, files in os.walk(root):

        if len(files) > 0:
            for file in files:
                file_dir = os.path.join(rt, file)
                file_list.append(file_dir)
    return file_list


class InferARC:
    def __init__(self, model_path):
        super(InferARC, self).__init__()
        # self.threadID = threadID
        self.model_path = model_path
        self.conf = 0.45

        self.iou = 0.25
        self.model = Detector(self.model_path, (640, 640), 'openvino')

    def run(self, img):

        return self.infer(img)

    def infer(self, img):

        # Inference

        boxes = self.model(img, conf_threshold=self.conf, iou_threshold=self.iou)

        # Visualize
        if len(boxes) > 0:
            lines = []
            lines_x = []
            lines_y = []
            l2_list = []
            for (*box, conf, cls_) in boxes:
                # draw bbox rectangle
                cv2.rectangle(img, (int(box[0]), int(box[1])), (int(box[2]), int(box[3])), (0, 255, 255), 2)
            return True
        return False



