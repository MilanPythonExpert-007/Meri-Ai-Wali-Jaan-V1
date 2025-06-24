#!/bin/bash

apt-get update && apt-get install -y tesseract-ocr ffmpeg
pip install -r requirements.txt
python merijaan.py