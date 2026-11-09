"""
DXF -> DWG 변환 (Windows PC에서 실행)

필요: ODA File Converter (Open Design Alliance 무료 변환기) 설치
      https://www.opendesign.com/guestfiles/oda_file_converter
사용: pip install ezdxf
      python to_dwg.py datacenter_dummy.dxf            -> datacenter_dummy.dwg (AutoCAD 2018 형식)
대안: AutoCAD / DWG TrueView 에서 DXF 를 열고 '다른 이름으로 저장' -> DWG
"""
import sys
from pathlib import Path

from ezdxf.addons import odafc

src = Path(sys.argv[1] if len(sys.argv) > 1 else "datacenter_dummy.dxf")
dst = src.with_suffix(".dwg")
if not odafc.is_installed():
    sys.exit("ODA File Converter 가 설치되어 있지 않습니다. 위 주석의 링크에서 설치하세요.")
odafc.convert(src, dst, version="R2018", replace=True)
print("saved", dst)
