"""Computer-vision tools for LLM Agents (video/image processing & understanding)."""

from importlib.metadata import version

from viseeker.image_describe import describe_image
from viseeker.video_adaptive_compress import adaptive_compress_video
from viseeker.video_convert_mp4 import convert_to_mp4
from viseeker.video_describe import describe_video
from viseeker.video_keyframes import extract_video_keyframes
from viseeker.video_metadata import extract_video_metadata
from viseeker.video_remove_audio import remove_video_audio
from viseeker.video_resize import resize_video
from viseeker.video_split import split_video

try:
    __version__ = version(__name__)
except Exception:
    __version__ = "0.1.0"

__all__ = [
    "__version__",
    "adaptive_compress_video",
    "convert_to_mp4",
    "describe_image",
    "describe_video",
    "extract_video_keyframes",
    "extract_video_metadata",
    "remove_video_audio",
    "resize_video",
    "split_video",
]
