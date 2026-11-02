"""Small, replaceable classifier baseline for phase and quality tasks."""
import numpy as np
import torch
from torch import nn
from PIL import Image, ImageOps

class TinyClassifier(nn.Module):
    def __init__(self, classes):
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(3, 24, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(24, 48, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(48, 96, 3, padding=1), nn.ReLU(), nn.AdaptiveAvgPool2d(1))
        self.head = nn.Linear(96, classes)

    def forward(self, x):
        return self.head(self.features(x).flatten(1))

def tensor(image):
    image = ImageOps.pad(image.convert('RGB'), (256, 256), method=Image.Resampling.LANCZOS, color=(240, 240, 240))
    return torch.from_numpy(np.array(image, dtype=np.float32) / 255).permute(2, 0, 1)
