import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The existing training dependency directory targets Python 3.10. The separate
# analysis directory targets 3.12 and must never shadow the training runtime.
if sys.version_info[:2] == (3, 10) and (ROOT / '_train_deps').exists():
    sys.path.insert(0, str(ROOT / '_train_deps'))
os.environ.setdefault('YOLO_CONFIG_DIR', str(ROOT / 'workbench' / 'data' / 'yolo'))
os.environ.setdefault('YOLO_OFFLINE', 'true')
DATA = Path(os.environ.get('CHROMOSOME_DATA', ROOT / 'workbench' / 'data'))
STAGES = ['quality', 'clusters', 'phase', 'instances', 'outliers']
LABELS = {
    'quality': ['clear', 'blurry', 'unusable'],
    'clusters': ['cluster', 'exclude'],
    'phase': ['mi', 'non_mi', 'unreadable', 'unknown'],
    'instances': ['chromosome', 'exclude'],
    'outliers': ['outlier', 'normal', 'unknown'],
}

def model_paths():
    return {
        'clusters': Path(os.environ.get('CLUSTER_WEIGHTS', ROOT / 'chromosome_detector/weights/chromosome_cluster.pt')),
        'phase': Path(os.environ.get('PHASE_WEIGHTS', ROOT / 'mi_training_initial_frozen_backbone/mi_classifier.pt')),
        'quality': Path(os.environ.get('QUALITY_WEIGHTS', DATA / 'models/quality.pt')),
    }
