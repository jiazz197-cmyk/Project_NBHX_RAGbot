"""Convert PDF bytes or streams to raster images (per page)."""
import io
import tempfile
from pathlib import Path
from typing import List, Union, Tuple
from pdf2image import convert_from_bytes, convert_from_path
from PIL import Image


def pdf_to_images(
    file_data: Union[bytes, io.IOBase],
    dpi: int = 200,
    fmt: str = "JPEG",
    quality: int = 85,
    first_page: int = None,
    last_page: int = None
) -> List[Tuple[bytes, str]]:
    """One tuple per page: (bytes, suggested_filename)."""
    
    try:
        if not isinstance(file_data, bytes):
            file_data.seek(0)
            pdf_bytes = file_data.read()
        else:
            pdf_bytes = file_data
        
        images = convert_from_bytes(
            pdf_bytes,
            dpi=dpi,
            first_page=first_page,
            last_page=last_page,
            fmt=fmt
        )
        
        result = []
        for idx, image in enumerate(images, start=1):
            img_buffer = io.BytesIO()
            
            if fmt.upper() == "JPEG" or fmt.upper() == "JPG":
                if image.mode in ("RGBA", "LA", "P"):
                    rgb_image = Image.new("RGB", image.size, (255, 255, 255))
                    rgb_image.paste(image, mask=image.split()[-1] if image.mode == "RGBA" else None)
                    image = rgb_image
                
                image.save(img_buffer, format="JPEG", quality=quality, optimize=True)
                file_extension = "jpg"
            else:
                image.save(img_buffer, format=fmt, quality=quality, optimize=True)
                file_extension = fmt.lower()
            
            img_bytes = img_buffer.getvalue()
            img_buffer.close()
            
            suggested_filename = f"page_{idx:03d}.{file_extension}"
            
            result.append((img_bytes, suggested_filename))
        
        return result
    
    except Exception as e:
        raise Exception(f"PDF conversion failed: {str(e)}")


def pdf_to_single_image(
    file_data: Union[bytes, io.IOBase],
    dpi: int = 200,
    quality: int = 85,
    page_number: int = 1
) -> Tuple[bytes, str]:
    """Wrapper: pdf_to_images with first_page == last_page == page_number."""
    
    results = pdf_to_images(
        file_data=file_data,
        dpi=dpi,
        quality=quality,
        first_page=page_number,
        last_page=page_number
    )
    
    if not results:
        raise Exception(f"Failed to convert page {page_number}")
    
    return results[0]


def get_pdf_page_count(file_data: Union[bytes, io.IOBase]) -> int:
    """Low-DPI convert_from_bytes; len(images) as page count."""
    try:
        if not isinstance(file_data, bytes):
            file_data.seek(0)
            pdf_bytes = file_data.read()
        else:
            pdf_bytes = file_data
        
        images = convert_from_bytes(pdf_bytes, dpi=72)
        return len(images)
    
    except Exception as e:
        raise Exception(f"Failed to get PDF page count: {str(e)}")

