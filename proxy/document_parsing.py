"""Bounded text extraction; reject unsupported binary formats instead of indexing garbage."""
import io
import zipfile
from xml.etree import ElementTree as ET

from fastapi import HTTPException
from pypdf import PdfReader


def extract(content: bytes, media_type: str) -> str:
    media_type=media_type.split(';',1)[0].strip().lower()
    if media_type=='application/pdf':
        try:
            reader=PdfReader(io.BytesIO(content))
            if len(reader.pages)>1000:
                raise ValueError('PDF exceeds 1000 pages')
            text='\n\n'.join(f'[Page {i+1}]\n{page.extract_text() or ""}' for i,page in enumerate(reader.pages))
            if not any((page.extract_text() or '').strip() for page in reader.pages):
                raise ValueError('PDF has no extractable text; OCR is required before ingestion')
            return text
        except Exception as error:
            raise HTTPException(422,'Unable to extract PDF text; supply a text/OCR version') from error
    if media_type in {'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                       'application/vnd.openxmlformats-officedocument.presentationml.presentation'}:
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                infos=archive.infolist()
                if len(infos)>10000 or sum(i.file_size for i in infos)>50*1024*1024:
                    raise ValueError('Expanded Office archive exceeds limit')
                names=['word/document.xml'] if 'wordprocessingml' in media_type else sorted(
                    [i.filename for i in infos if i.filename.startswith('ppt/slides/slide') and i.filename.endswith('.xml')],
                    key=lambda name:int(name.rsplit('slide',1)[1].split('.')[0]))
                parts=[]
                for name in names:
                    xml=archive.read(name)
                    if b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():
                        raise ValueError('XML declarations are forbidden')
                    root=ET.fromstring(xml)
                    paragraphs=[''.join(n.text or '' for n in p.iter() if n.tag.rsplit('}',1)[-1]=='t')
                                for p in root.iter() if p.tag.rsplit('}',1)[-1]=='p']
                    parts.append(f'[{name}]\n'+'\n'.join(paragraphs))
                if not parts: raise ValueError('No readable document parts')
                return '\n\n'.join(parts)
        except Exception as error:
            raise HTTPException(422,'Invalid or oversized Office document') from error
    if not (media_type.startswith('text/') or media_type in {'application/json','application/xml','application/octet-stream'}):
        raise HTTPException(415,'Unsupported document format; provide PDF, DOCX, PPTX, or UTF-8 text')
    try:
        text=content.decode('utf-8-sig')
        if '\x00' in text: raise ValueError('Binary content')
        return text
    except (UnicodeError,ValueError) as error:
        raise HTTPException(415,'Binary content cannot be indexed as text') from error
