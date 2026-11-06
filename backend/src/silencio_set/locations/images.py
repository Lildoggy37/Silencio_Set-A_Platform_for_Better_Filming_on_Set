"""Static scouting photos; decoded pixels are re-encoded without source metadata."""
from dataclasses import dataclass
from io import BytesIO
import json
import subprocess
import sys
import warnings

from PIL import Image, ImageOps

from silencio_set.accounts.errors import AuthError

MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 25_000_000
CONTENT_TYPES = {'jpeg':'image/jpeg', 'png':'image/png', 'webp':'image/webp'}


class PhotoProcessingError(Exception):
    pass


@dataclass(frozen=True)
class ProcessedPhoto:
    content: bytes
    preview: bytes
    file_format: str
    width_px: int
    height_px: int


def normalize_photo(data):
    if not data: raise PhotoProcessingError('PHOTO_EMPTY')
    if len(data) > MAX_BYTES: raise PhotoProcessingError('PHOTO_TOO_LARGE')
    if not (data.startswith(b'\xff\xd8\xff') or data.startswith(b'\x89PNG\r\n\x1a\n') or data[:4]==b'RIFF' and data[8:12]==b'WEBP'):
        raise PhotoProcessingError('PHOTO_TYPE_UNSUPPORTED')
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(BytesIO(data)) as source:
            if source.format not in {'JPEG','PNG','WEBP'}: raise PhotoProcessingError('PHOTO_TYPE_UNSUPPORTED')
            file_format = source.format.lower()
            if source.format in {'PNG','WEBP'} and source.is_animated: raise PhotoProcessingError('PHOTO_ANIMATED')
            if source.width * source.height > MAX_PIXELS: raise PhotoProcessingError('PHOTO_PIXEL_LIMIT')
            source.load()
            oriented = ImageOps.exif_transpose(source)
            mode = 'RGBA' if 'A' in oriented.getbands() or 'transparency' in oriented.info else 'RGB'
            converted = oriented.convert(mode)
            clean = Image.new(mode, converted.size)
            clean.paste(converted)
            oriented.close(); converted.close()
            width, height = clean.size
            output = BytesIO()
            if file_format == 'jpeg': clean.save(output, 'JPEG', quality=90, optimize=True)
            elif file_format == 'png': clean.save(output, 'PNG', optimize=True)
            else: clean.save(output, 'WEBP', quality=90, method=4)
            content = output.getvalue()
            if len(content) > MAX_BYTES: raise PhotoProcessingError('PHOTO_RESULT_TOO_LARGE')
            clean.thumbnail((1280,1280),Image.Resampling.LANCZOS)
            preview = BytesIO(); clean.save(preview,'WEBP',quality=85,method=4)
            clean.close()
            return ProcessedPhoto(content=content,preview=preview.getvalue(),file_format=file_format,width_px=width,height_px=height)


def normalize_in_process(data):
    try:
        result = subprocess.run([sys.executable,'-m','silencio_set.locations.images'],input=data,
            stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=30,check=False)
    except subprocess.TimeoutExpired:
        raise AuthError(422,'PHOTO_PROCESS_TIMEOUT','图片处理超时，请缩小图片后再上传') from None
    if result.returncode != 0: raise AuthError(422,'PHOTO_INVALID','图片无法解码，请检查文件')
    header, _, body = result.stdout.partition(b'\n')
    metadata = json.loads(header)
    if 'error' in metadata:
        code=metadata['error']
        status=413 if code in {'PHOTO_TOO_LARGE','PHOTO_RESULT_TOO_LARGE'} else 415 if code in {'PHOTO_TYPE_UNSUPPORTED','PHOTO_ANIMATED'} else 422
        raise AuthError(status,code,'图片无法处理，请检查图片格式和大小')
    length=metadata.pop('content_length')
    return ProcessedPhoto(content=body[:length],preview=body[length:],**metadata)


if __name__ == '__main__':
    try:
        image = normalize_photo(sys.stdin.buffer.read(MAX_BYTES+1))
        metadata=dict(content_length=len(image.content),file_format=image.file_format,width_px=image.width_px,height_px=image.height_px)
        body=image.content+image.preview
    except PhotoProcessingError as error:
        metadata,body={'error':str(error)},b''
    except (Image.DecompressionBombWarning,Image.DecompressionBombError):
        metadata,body={'error':'PHOTO_PIXEL_LIMIT'},b''
    except Exception:
        metadata,body={'error':'PHOTO_INVALID'},b''
    sys.stdout.buffer.write(json.dumps(metadata).encode('utf-8')+b'\n'+body)
