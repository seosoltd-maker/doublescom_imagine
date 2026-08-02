import streamlit as st
import os
import re
import io
import zipfile
import requests
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
import pandas as pd

# OCR & Translation Imports
import easyocr
from deep_translator import GoogleTranslator

# -------------------------------------------------------------------
# 페이지 기본 설정
# -------------------------------------------------------------------
st.set_page_config(
    page_title="동화님의 쿠팡 자동 등록 지원 서비스_번역확인",
    page_icon="🚀",
    layout="centered"
)

# -------------------------------------------------------------------
# EasyOCR 및 번역기 캐싱 (서버 메모리 절약 & 속도 향상)
# -------------------------------------------------------------------
@st.cache_resource
def get_ocr_reader():
    return easyocr.Reader(['ch_sim', 'en'], gpu=False)

@st.cache_resource
def get_translator():
    return GoogleTranslator(source='auto', target='ko')

# -------------------------------------------------------------------
# 사이드바: 관리자 인증 & 기본 설정
# -------------------------------------------------------------------
st.sidebar.title("🔒 관리자 인증")
# 💡 여기서 사용할 비밀번호를 설정하세요!
ADMIN_PASSWORD = "wnsclqkqh123" 

user_pw = st.sidebar.text_input("접근 비밀번호를 입력하세요", type="password")

if user_pw != ADMIN_PASSWORD:
    st.title("🚀 동화님의 쿠팡 자동 등록 지원 도구")
    st.warning("⚠️ 인증이 필요합니다. 사이드바에 올바른 비밀번호를 입력해 주세요.")
    st.info("💡 기본 비밀번호는 `admin` 으로 설정되어 있습니다. (코드에서 변경 가능)")
    st.stop()

st.sidebar.success("✅ 인증 성공! 서비스를 이용할 수 있습니다.")

# -------------------------------------------------------------------
# 메인 화면
# -------------------------------------------------------------------
st.title("🚀 쿠팡 자동 등록 지원 도구_번역확인")
st.caption("해외 상품 크롤링, 1000x1000 최적화, AI 한국어 번역, 마진 계산기")

st.markdown("---")

# 1. 상품 정보 입력 Section
st.subheader("1. 상품 정보 및 원가 입력")
col1, col2 = st.columns([2, 1])

with col1:
    url = st.text_input("상품 URL", placeholder="https://aliexpress.com/item/...")
with col2:
    orig_price = st.number_input("상품 원가 ($/¥)", min_value=0.0, value=10.0, step=0.5)

col3, col4 = st.columns(2)
with col3:
    shipping = st.number_input("유료 배송비 ($/¥)", min_value=0.0, value=0.0, step=0.5)
with col4:
    rate = st.number_input("적용 환율 (원)", min_value=1.0, value=1570.0, step=10.0)

# 2. 마진 계산 Section
st.subheader("2. 마진 및 수수료 설정")
col5, col6 = st.columns(2)

with col5:
    margin = st.number_input("목표 마진 (원)", min_value=0.0, value=2000.0, step=500.0)
with col6:
    fee_rate_input = st.number_input("쿠팡 수수료율 (%)", min_value=0.0, max_value=99.0, value=12.0, step=0.5)

# 판매가 자동 계산
fee_rate = fee_rate_input / 100.0
if fee_rate < 1.0:
    target_price = int(round(((orig_price + shipping) * rate + margin) / (1.0 - fee_rate), -1))
else:
    target_price = 0

st.info(f"💡 **예상 쿠팡 판매가:** `{target_price:,} 원`")

# 3. AI 번역 옵션 선택
st.subheader("3. 이미지 처리 옵션")
do_translation = st.checkbox("🤖 AI 한국어 이미지 번역 진행 (EasyOCR + Google)", value=True)

st.markdown("---")

# -------------------------------------------------------------------
# 이미지 수집 및 번역 실행 로직
# -------------------------------------------------------------------
def process_images(url, do_translate):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"
    }

    st.text("🌐 웹페이지 소스 수집 중...")
    response = requests.get(url, headers=headers, timeout=15)
    if response.status_code != 200:
        raise Exception(f"페이지를 불러올 수 없습니다. (상태 코드: {response.status_code})")

    raw_html = response.text

    # Hidden descUrl 추적
    desc_matches = re.findall(r'(?:https?:)?//(?:desc|win\.item|desc\.alicdn)[^\s\'"<>]+', raw_html)
    detail_html = ""
    if desc_matches:
        desc_url = desc_matches[0]
        if desc_url.startswith('//'):
            desc_url = 'https:' + desc_url
        try:
            desc_res = requests.get(desc_url, headers=headers, timeout=15)
            if desc_res.status_code == 200:
                detail_html = desc_res.text
        except:
            pass

    combined_html = raw_html + "\n" + detail_html
    pattern = r'(?:https?:)?//[^\s\'"<>]+?\.(?:jpg|jpeg|png|webp)'
    found_matches = re.findall(pattern, combined_html, re.IGNORECASE)

    img_urls = []
    for img_url in found_matches:
        if img_url.startswith('//'):
            img_url = 'https:' + img_url
        img_url = re.sub(r'_\d+x\d+.*$', '', img_url)
        img_url = re.sub(r'_\.webp$', '', img_url)

        skip_keywords = ['logo', 'icon', 'avatar', 'flag', 'badge', '16x16', '32x32', '50x50', 'ae01.alicdn.com/kf/S']
        if any(kw in img_url.lower() for kw in skip_keywords):
            continue
        if img_url not in img_urls:
            img_urls.append(img_url)

    if not img_urls:
        raise Exception("이미지를 찾지 못했습니다.")

    processed_images = [] # (filename, bytes_data)
    reader = get_ocr_reader() if do_translate else None
    translator = get_translator() if do_translate else None

    status_text = st.empty()
    progress_bar = st.progress(0)

    total_target = min(len(img_urls), 15)
    
    for idx, img_url in enumerate(img_urls[:total_target], start=1):
        status_text.text(f"📸 이미지 [{idx}/{total_target}] 수집 및 최적화 중...")
        try:
            img_res = requests.get(img_url, headers=headers, timeout=10)
            if img_res.status_code == 200 and len(img_res.content) > 5000:
                img = Image.open(io.BytesIO(img_res.content)).convert("RGB")
                img = img.resize((1000, 1000), Image.Resampling.LANCZOS)

                # 번역 실행
                if do_translate:
                    status_text.text(f"🎨 이미지 [{idx}/{total_target}] AI 한국어 번역 중...")
                    img_byte_arr = io.BytesIO()
                    img.save(img_byte_arr, format='JPEG', quality=95)
                    img_bytes = img_byte_arr.getvalue()

                    results = reader.readtext(img_bytes)
                    if results:
                        draw = ImageDraw.Draw(img)
                        font_path = "C:\\Windows\\Fonts\\malgun.ttf"
                        
                        for bbox, text, prob in results:
                            if prob < 0.35 or not text.strip():
                                continue
                            try:
                                trans_text = translator.translate(text)
                                if not trans_text or trans_text.strip() == text.strip():
                                    continue
                            except:
                                continue

                            xs = [p[0] for p in bbox]
                            ys = [p[1] for p in bbox]
                            min_x, max_x, min_y, max_y = int(min(xs)), int(max(xs)), int(min(ys)), int(max(ys))
                            box_h = max_y - min_y
                            if box_h < 10:
                                continue

                            draw.rectangle([max(0, min_x-3), max(0, min_y-3), min(img.width, max_x+3), min(img.height, max_y+3)], fill=(255, 255, 255))
                            font_size = max(12, int(box_h * 0.75))
                            try:
                                font = ImageFont.truetype(font_path, font_size)
                            except:
                                font = ImageFont.load_default()
                            draw.text((min_x, min_y), trans_text, fill=(0, 0, 0), font=font)

                out_buffer = io.BytesIO()
                img.save(out_buffer, format="JPEG", quality=95)
                processed_images.append((f"image_{len(processed_images)+1}.jpg", out_buffer.getvalue()))

        except Exception as e:
            pass

        progress_bar.progress(idx / total_target)

    status_text.text("✅ 처리 완료!")
    return processed_images

# 작업 시작 버튼
if st.button("🚀 이미지 수집 및 처리 시작", type="primary", use_container_width=True):
    if not url:
        st.error("상품 URL을 입력해 주세요.")
    else:
        try:
            with st.spinner("작업을 진행하고 있습니다... 잠시만 기다려 주세요."):
                images = process_images(url, do_translation)
                
            if images:
                st.success(f"🎉 총 {len(images)}개의 이미지가 성공적으로 수집/처리되었습니다!")

                # 1. ZIP 압축 파일 생성
                zip_buffer = io.BytesIO()
                with zipfile.ZipFile(zip_buffer, "w") as zf:
                    for filename, data in images:
                        zf.writestr(filename, data)
                
                # 2. 엑셀 파일 생성
                df = pd.DataFrame([{
                    "수집일시": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "상품URL": url,
                    "원가(외화)": orig_price,
                    "배송비(외화)": shipping,
                    "환율": rate,
                    "마진(원)": margin,
                    "수수료율(%)": fee_rate_input,
                    "최종판매가(원)": target_price,
                    "수집이미지수": len(images),
                    "번역여부": "Y" if do_translation else "N"
                }])
                
                excel_buffer = io.BytesIO()
                with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
                    df.to_excel(writer, index=False)

                st.markdown("### 📥 결과물 다운로드")
                col_dl1, col_dl2 = st.columns(2)
                
                with col_dl1:
                    st.download_button(
                        label="📦 이미지 ZIP 다운로드",
                        data=zip_buffer.getvalue(),
                        file_name=f"product_images_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip",
                        mime="application/zip",
                        use_container_width=True
                    )
                
                with col_dl2:
                    st.download_button(
                        label="📊 엑셀 정산기록 다운로드",
                        data=excel_buffer.getvalue(),
                        file_name=f"product_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True
                    )

        except Exception as e:
            st.error(f"❌ 오류가 발생했습니다: {str(e)}")
