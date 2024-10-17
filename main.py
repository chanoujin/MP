import snap7
import cv2
import numpy as np
import onnxruntime as ort
from sqlalchemy.orm import sessionmaker

from SQL import MPDatabase
from sqlalchemy import create_engine
from obs import InferARC
class MP:
    def __init__(self,onnx_model,video_path):
        self.client = snap7.client.Client()
        self.client.connect('192.168.0.1', 0, 1)
        self.video_path = video_path
        self.engine = create_engine('sqlite:///'+onnx_model)
        DBsession = sessionmaker(bind=self.engine)
        self.session = DBsession()



        # 构建推理引擎
        self.onnx_model = onnx_model
        self.InferARC = InferARC(self.onnx_model)

    def get_size(self,image):
        width, height = 0,0
        return width, height

    def detect(self,image):
        return self.InferARC.infer(image)

    def __call__(self):
        cap = cv2.VideoCapture(self.video_path)
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                print('video disconnected')
                break
            width,height = self.get_size(frame)
            result = self.detect(frame)
            self.client.write_area(snap7.types.Areas.DB,1,0,result)
            data = MPDatabase(img_name="",bubble_num=0,trace_num=0)
            self.session.add(data)
            self.session.commit()




