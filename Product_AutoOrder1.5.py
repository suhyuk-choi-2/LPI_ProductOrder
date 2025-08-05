# Product_AutoOrder_Final_v4_fixed_sync_fix.py
import streamlit as st
import pandas as pd
import numpy as np
import json
import os
import math
import datetime
from typing import Dict, Optional
from pathlib import Path
from io import BytesIO
import plotly.express as px

# --- 1. 기본 설정 및 스타일 ---
st.set_page_config(page_title="LPI TEAM 자동 발주량 계산 시스템", layout="wide")
st.markdown("""
<style>
.footer { position: fixed; left: 80px; bottom: 20px; font-size: 13px; color: #888; }
.total-cell { width: 100%; text-align: right; font-weight: bold; font-size: 1.1em; padding: 10px 0; }
</style>
""", unsafe_allow_html=True)
st.markdown('<div class="footer">by suhyuk (twodoong@gmail.com)</div>', unsafe_allow_html=True)


# --- 2. 설정 및 상수 정의 ---
SETTINGS_FILE = 'item_settings.json'
FILE_PATTERN = "현황*.xlsx"
COL_ITEM_CODE = '상품코드'
COL_ITEM_NAME = '상품명'
COL_SPEC = '규격'
COL_BARCODE = '바코드'
COL_UNIT_PRICE = '현구매단가'
COL_SUPPLIER = '매입처'
COL_SALES = '매출수량'
COL_STOCK = '현재고'
EXCLUDE_KEYWORDS = ['배송비', '첫 주문', '쿠폰', '개인결제', '마일리지']
INITIAL_DEFAULT_SETTINGS = {'lead_time': 15, 'safety_stock_rate': 10, 'addition_rate': 0, 'order_unit': 5, 'min_sales': 0}

# --- 3. 핵심 기능 함수 ---
def load_settings() -> Dict[str, Dict]:
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            settings = json.load(f)
            if "master_defaults" not in settings:
                settings["master_defaults"] = INITIAL_DEFAULT_SETTINGS.copy()
            else:
                if "min_sales" not in settings["master_defaults"]:
                     settings["master_defaults"]['min_sales'] = INITIAL_DEFAULT_SETTINGS['min_sales']

            for sup_settings in settings.get("defaults", {}).values():
                sup_settings.setdefault('min_sales', settings["master_defaults"]['min_sales'])
            for item_settings in settings.get("overrides", {}).values():
                item_settings.setdefault('min_sales', INITIAL_DEFAULT_SETTINGS['min_sales'])
            return settings
    return {"master_defaults": INITIAL_DEFAULT_SETTINGS.copy(), "defaults": {}, "overrides": {}}

def save_settings(settings: Dict[str, Dict]):
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(settings, f, ensure_ascii=False, indent=4)

def find_latest_file(directory: Path, pattern: str) -> Optional[Path]:
    try:
        files = list(directory.glob(pattern))
        if not files: return None
        return max(files, key=lambda p: p.stat().st_mtime)
    except Exception: return None

def get_min_sales_for_row(row: pd.Series, settings: Dict[str, Dict]) -> int:
    item_code = str(row.get(COL_ITEM_CODE, ''))
    supplier = str(row.get(COL_SUPPLIER, ''))
    master_defaults = settings.get("master_defaults", INITIAL_DEFAULT_SETTINGS)

    if item_code in settings.get("overrides", {}) and 'min_sales' in settings["overrides"][item_code]:
        return settings["overrides"][item_code]['min_sales']
    if supplier in settings.get("defaults", {}) and 'min_sales' in settings["defaults"][supplier]:
        return settings["defaults"][supplier]['min_sales']
    return master_defaults.get('min_sales', 0)

def get_settings_for_item(item_code: str, supplier: str, settings: Dict[str, Dict]) -> Dict:
    """특정 품목에 적용되는 최종 설정값을 계산합니다."""
    master_defaults = settings.get("master_defaults", INITIAL_DEFAULT_SETTINGS)
    supplier_defaults = settings.get("defaults", {}).get(supplier, {})
    item_overrides = settings.get("overrides", {}).get(str(item_code), {})
    
    # 우선순위: 개별 설정 > 매입처별 설정 > 마스터 기본값
    final_settings = {**master_defaults, **supplier_defaults, **item_overrides}
    return final_settings

def create_settings_export_data(df_filtered: pd.DataFrame, settings: Dict[str, Dict]) -> pd.DataFrame:
    """현재 필터된 데이터의 모든 품목에 대한 설정값을 포함한 데이터프레임을 생성합니다."""
    try:
        export_data = []
        
        for _, row in df_filtered.iterrows():
            item_code = str(row.get(COL_ITEM_CODE, ''))
            supplier = str(row.get(COL_SUPPLIER, ''))
            
            # 각 품목의 최종 설정값 계산
            final_settings = get_settings_for_item(item_code, supplier, settings)
            
            # 설정 출처 확인 (우선순위: 개별 > 매입처별 > 마스터)
            if item_code in settings.get("overrides", {}):
                setting_source = "개별 품목 설정"
            elif supplier in settings.get("defaults", {}):
                setting_source = "매입처별 기본값"
            else:
                setting_source = "마스터 기본값"
            
            export_row = {
                COL_ITEM_CODE: item_code,
                '리드타임(재발주기간)(일)': final_settings.get('lead_time', 15),
                '안전재고율(%)': final_settings.get('safety_stock_rate', 10),
                '가산율(%)': final_settings.get('addition_rate', 0),
                '발주단위': final_settings.get('order_unit', 5),
                '제외매출수량': final_settings.get('min_sales', 0),
                '설정구분': setting_source
            }
            export_data.append(export_row)
        
        return pd.DataFrame(export_data)
    
    except Exception as e:
        st.error(f"설정 데이터 생성 중 오류 발생: {e}")
        return pd.DataFrame()

def calculate_order_quantity(df: pd.DataFrame, settings: Dict[str, Dict], period_days: int) -> pd.DataFrame:
    results = []
    master_defaults = settings.get("master_defaults", INITIAL_DEFAULT_SETTINGS)
    default_settings = settings.get("defaults", {})
    override_settings = settings.get("overrides", {})

    for row in df.to_dict('records'):
        item_code = str(row.get(COL_ITEM_CODE, ''))
        supplier = str(row.get(COL_SUPPLIER, ''))
        final_settings = {k: v for k, v in {**master_defaults, **default_settings.get(supplier, {}), **override_settings.get(item_code, {})}.items() if k != 'min_sales'}

        lead_time = final_settings.get('lead_time', 0)
        safety_stock_rate = final_settings.get('safety_stock_rate', 0) / 100
        addition_rate = final_settings.get('addition_rate', 0) / 100
        order_unit = final_settings.get('order_unit', 1)
        if order_unit <= 0: order_unit = 1

        sales_quantity = row.get(COL_SALES, 0)
        current_stock = row.get(COL_STOCK, 0)
        row['추천 발주량'] = 0
        row['초과재고 수량'] = 0

        if period_days > 0:
            avg_daily_sales = sales_quantity / period_days
            sales_during_lead_time = avg_daily_sales * lead_time
            safety_stock = sales_during_lead_time * safety_stock_rate
            reorder_point = sales_during_lead_time + safety_stock
            base_order_quantity = reorder_point - current_stock

            if base_order_quantity <= 0:
                if current_stock > reorder_point * 2 and reorder_point > 0:
                    row['비고'] = "초과재고"
                    row['초과재고 수량'] = current_stock - math.ceil(reorder_point)
                else:
                    row['비고'] = "재고 충분"
            else:
                calculated_quantity = base_order_quantity * (1 + addition_rate)
                final_order_quantity = math.ceil(calculated_quantity / order_unit) * order_unit
                row['추천 발주량'] = int(final_order_quantity)
                if current_stock < final_order_quantity:
                    row['비고'] = "발주 필요 (긴급)"
                else:
                    row['비고'] = "발주 필요"

            row['재고 소진 예상일'] = current_stock / avg_daily_sales if avg_daily_sales > 0 else float('inf')
        else:
            row['비고'] = "기간 1일 이상"
            row['재고 소진 예상일'] = float('inf')

        row['적용된 설정'] = f"L:{lead_time} S:{safety_stock_rate*100:.0f}% A:{addition_rate*100:.0f}% U:{order_unit}"
        results.append(row)
    return pd.DataFrame(results)

def style_remarks(val):
    if val in ['발주 필요 (긴급)', '악성 초과재고']:
        return 'color: #D32F2F; font-weight: bold;'
    return ''

# --- 4. Streamlit UI 구성 ---
title_col1, title_col2 = st.columns([3, 1])
with title_col1:
    st.title("LPI TEAM 자동 발주량 계산 시스템 v1.4")

with title_col2:
    btn_cols = st.columns(2)
    with btn_cols[0]:
        if st.button("📖 시스템 설명"):
            @st.dialog("시스템 설명")
            def show_description():
                st.markdown("""
                ### 📂 1. 입력 항목 설명
                • **시작일/종료일**: 매출 분석 기간 설정 (기본: 30일)  
                • **제외 매출수량**: 입력값 미만 품목은 계산에서 제외  
                • **리드타임(재발주 기간)(일)**: 발주 후 입고까지 소요 기간(재발주 기간)  
                • **안전재고율(%)**: 리드타임(재발주 기간) 동안 예상 매출의 추가 보유 비율  
                • **가산율(%)**: 계산된 발주량에 추가하는 여유분 비율  
                • **발주단위**: 발주 시 최소 단위 (5개 단위 등)  
                
                ### 📊 2. 긴급 발주 품목 비율 설명
                **■ 안전재고 적용 상세 조건:** • 계산식: (일일 평균 매출 수량 × 리드타임(재발주 기간)) × 안전재고율  
                • 목적: 모자랄 것을 대비하는 추가 여유분  
                • 예시: 일일 20개 판매, 리드타임(재발주 기간) 15일, 안전재고율 10%  
                　→ 기본 추전 발주량 = 20 × 15 = 300개  
                　→ 안전재고 = 300 × 0.1 = 30개 (추가 여유분)  
                　→ 총 추전 발주량 = 300 + 30 = 330개  
                
                **■ 긴급 발주 조건:** • 현재고 < 최종 추천 발주량 (발주량이 클수록 긴급)  
                • 예시: 현재고 250개 < 최종 추천 발주량 350개 → 긴급 발주  
                
                **■ 표시 비율 설정:** • 긴급 발주 품목 중 표시할 상위 비율  
                • 정렬 기준: 추천 발주량이 많은 순서  
                • 예시: 긴급 품목 20개 × 25% = 상위 5개 표시  
                　　　긴급 품목 8개 × 50% = 상위 4개 표시  
                
                ### 🧮 3. 발주 추천 상품 계산 조건
                **■ 계산 공식:** • 일일 평균 매출 수량수량 = 총 매출수량 ÷ 분석기간  
                • 기본 추전 발주량 = 일일 평균 매출 수량 × 리드타임(재발주 기간)  
                • 안전재고 = 기본 추전 발주량 × 안전재고율 (추가 여유분)  
                • 총 추전 발주량 = 기본 추전 발주량 + 안전재고  
                • 기본 발주량 = 총 추전 발주량 - 현재고  
                • 최종 발주량 = 기본 발주량 × (1 + 가산율) → 발주단위로 반올림  
                
                **■ 계산 예시:** • 매출수량: 600개(30일), 현재고: 80개, 리드타임(재발주 기간): 15일, 안전재고율: 10%, 가산율: 5%, 발주단위: 10개  
                • 일일 평균: 600÷30 = 20개  
                • 기본 추전 발주량: 20×15 = 300개  
                • 안전재고: 300×0.1 = 30개 (추가 여유분)  
                • 총 추전 발주량: 300+30 = 330개  
                • 기본 발주량: 330-80 = 250개  
                • 최종 발주량: 250×1.05 = 262.5 → 270개(10개 단위)  
                
                **■ 비고(발주 표시) 판정 기준:** • 발주 필요 (긴급): 현재고 < 최종 추천 발주량  
                • 발주 필요: 기본 발주량 > 0, 현재고 ≥ 최종 추천 발주량  
                • 재고 충분: 기본 발주량 ≤ 0  
                • 초과재고: 현재고 > 총 추전 발주량 × 2  
                
                ### ⚙️ 4. 개별 품목별 설정 설명
                **■ 설정 우선순위:** 1. 개별 품목 설정 (최우선)  
                2. 매입처별 기본값  
                3. 마스터 기본값  
                
                **■ 사용법 예시:** • 특정 상품(A001)은 리드타임(재발주 기간)이 다른 상품보다 길어서 25일로 설정  
                • 매입처 기본값: 리드타임(재발주 기간) 15일 → 개별 설정: 리드타임(재발주 기간) 25일  
                • 계산 시 A001만 25일 적용, 나머지는 15일 적용  
                
                **■ 실제 적용:** • 발주량 계산 실행 후 상품코드 검색  
                • 개별 설정값 입력 후 저장  
                • 재계산 시 개별 설정값 적용  
                • 기본값 복원으로 개별 설정 삭제 가능  
                
                **■ 설정값 일괄 다운로드:** • 현재 선택된 매입처의 모든 품목 설정값을 엑셀로 다운로드  
                • 파일명 형식: `매입처명_품목별설정값_20250626_174931.xlsx`  
                • 포함 정보: 상품코드, 각종 설정값, 설정구분  
                • 설정구분: 마스터 기본값 / 매입처별 기본값 / 개별 품목 설정  
                • 다운로드 위치: PC의 다운로드 폴더에 자동 저장  
                
                ### 📦 5. 초과재고 현황 계산 조건
                **■ 초과재고 판정:** 현재고 > 총 추전 발주량 × 2  
                
                **■ 각 컬럼 계산 예시:** • 현재고: 800개, 총 추전 발주량: 330개, 매출수량: 600개(30일), 현구매단가: 1,000원  
                • 초과재고 수량 = 800 - 330 = 470개  
                • 초과재고 비율 = 800 ÷ 600 = 1.3배  
                • 초과재고 금액 = 470 × 1,000 = 470,000원  
                • 재고 소진 예상일 = 800 ÷ 20(일일매출) = 40일  
                
                **■ 악성/일반 구분:** • 전체 초과재고 비율의 중간값을 기준으로 분류  
                • 예시: 중간값이 2.0배인 경우  
                　→ 2.0배 이상: 악성 초과재고 (빨간색 표시)  
                　→ 2.0배 미만: 일반 초과재고  
                """)
            show_description()

    with btn_cols[1]:
        if st.button("📋 사용 메뉴얼"):
            @st.dialog("사용자 메뉴얼")
            def show_user_manual():
                st.markdown("""
                ### **LPI TEAM 자동 발주량 계산 시스템 - 사용자 메뉴얼 (v1.0)**

                안녕하세요! LPI TEAM 자동 발주량 계산 시스템 사용을 환영합니다.
                이 시스템은 복잡한 기간별 매출수량 현황과 현재고 데이터를 분석하여, 어떤 상품을 얼마나 발주해야 할지 자동으로 추천해 줍니다. 
                이 메뉴얼을 차근차근 따라 하시면 누구나 쉽게 전문가처럼 발주, 재고 관리를 할 수 있습니다.

                #### **1. 시작 전 준비사항: '엑셀 파일' 준비하기**

                시스템을 사용하기 위해 가장 먼저 필요한 것은 **매출 데이터가 담긴 엑셀 파일**입니다.

                1.  **필요한 파일**: 사내 시스템에서 다운로드한 **`상품별 매출현황`** 엑셀 파일이 필요합니다.
                2.  **파일 이름**: 시스템이 파일을 자동으로 찾을 수 있도록, 파일 이름은 항상 **`현황`**이라는 단어로 시작해야 합니다. (예: `현황(2025-06-24).xlsx`)
                3.  **파일 위치**: 다운로드한 파일을 PC의 **`다운로드`** 폴더에 그대로 두세요. 시스템이 자동으로 그 위치에서 최신 파일을 찾아냅니다.
                4.  **필수 데이터 확인**: 엑셀 파일 안에 아래 **8가지 정보(컬럼)**가 반드시 포함되어 있는지 확인해주세요. 이름이 하나라도 다르면 시스템이 인식할 수 없습니다.
                    * `상품코드`
                    * `상품명`
                    * `규격`
                    * `바코드`
                    * `매출수량`
                    * `현구매단가`
                    * `현재고`
                    * `매입처`

                > **✅ 체크포인트**: `다운로드` 폴더에 `현황`으로 시작하는, 8가지 컬럼이 모두 포함된 엑셀 파일이 준비되었나요? 그럼 다음 단계로 넘어갈 준비가 되었습니다!

                ---

                #### **2. 기본 사용 흐름: 4단계만 따라 하세요!**

                ##### **▶ 1단계: 분석할 파일과 기간 선택하기**

                1.  프로그램을 실행하면 가장 먼저 보이는 **[1. 분석 대상 파일 및 기간 설정]** 섹션을 확인합니다.
                2.  시스템이 `다운로드` 폴더에서 파일을 제대로 찾았다면, 초록색 메시지로 **"✅ 자동으로 찾은 최신 파일: ..."** 이라고 표시됩니다.
                    * 만약 파일을 못 찾거나 다른 파일을 쓰고 싶다면, **'수동으로 파일 업로드'** 버튼을 눌러 직접 파일을 선택할 수 있습니다.
                3.  **'시작일'**과 **'종료일'**을 설정합니다. 이 기간 동안의 매출 데이터를 바탕으로 발주량을 계산하게 됩니다. (기본 30일)

                ##### **▶ 2단계: 발주량 계산 실행하기**

                1.  설정이 완료되었으면, 파란색 **`🚀 발주량 계산 실행`** 버튼을 힘차게 눌러주세요!
                2.  "데이터를 분석하고 있습니다..." 메시지와 함께 시스템이 열심히 계산을 시작합니다.
                3.  잠시 후 계산이 완료되면 아래에 결과가 나타납니다.

                ##### **▶ 3단계: 결과 확인 및 분석하기**

                계산 결과는 크게 3부분으로 나뉩니다.

                * **① `📊 요약 대시보드`**: 전체적인 상황을 한눈에 파악할 수 있습니다.
                    * **추천 품목 수**: 발주가 필요한 상품이 총 몇 개인지 보여줍니다.
                    * **추천 수량**: 발주해야 할 상품들의 총수량입니다.
                    * **예상 금액**: 추천된 수량만큼 발주했을 때 예상되는 총비용입니다.

                * **② `긴급 발주 Top ... 그래프`**: 지금 당장 발주해야 할 **가장 시급한 상품**들을 보여줍니다.
                    * '긴급'의 의미는 '현재 재고'가 '추천된 발주량'보다도 적은 상태를 말합니다. 즉, 재고 소진이 임박했다는 뜻입니다.
                    * 그래프의 막대가 높을수록 더 많이, 더 시급하게 발주해야 하는 상품입니다.

                * **③ `📑 발주 추천 상품` 목록**: 발주가 필요한 모든 상품의 상세 목록입니다.
                    * **재고 소진 예상일**: 현재 재고가 며칠 안에 소진될지 예측한 날짜입니다. 숫자가 작을수록 위험합니다.
                    * **추천 발주량**: 시스템이 계산한 최적의 발주 수량입니다.
                    * **비고**: 상품의 재고 상태를 표시합니다.
                        * `발주 필요 (긴급)`: **(가장 중요!)** 즉시 발주가 필요한 위험 상태입니다.
                        * `발주 필요`: 지금 발주해야 할 상품입니다.
                        * `재고 충분`: 아직은 발주할 필요가 없습니다.
                        * `초과재고`: 재고가 너무 많아 관리가 필요한 상품입니다. (별도 '초과재고 현황' 목록에서 확인)
                    * **적용된 설정**: 어떤 기준으로 이 발주량이 계산되었는지 보여줍니다. (L: 리드타임(재발주 기간), S: 안전재고율, A: 가산율, U: 발주단위)

                ##### **▶ 4단계: 결과 다운로드 및 활용하기**

                1.  `발주 추천 상품` 목록 하단의 **`📥 엑셀 다운로드`** 버튼을 클릭하세요.
                2.  발주가 필요한 상품 목록 전체가 엑셀 파일로 저장됩니다.
                3.  이 엑셀 파일을 기준으로 실제 발주 업무를 진행하면 됩니다.

                ---

                #### **3. 심화 기능: 우리 회사에 딱 맞는 맞춤 설정하기**

                시스템의 계산 방식을 우리 회사의 상황에 맞게 더 정밀하게 조정할 수 있습니다. 설정은 **[2. 발주 설정 관리]** 섹션에서 할 수 있으며, **개별 품목 설정 > 매입처별 설정 > 마스터 기본값** 순서로 우선 적용됩니다.

                * **`[마스터]` 시스템 전체 기본값 설정**
                    * 모든 상품에 공통으로 적용되는 가장 기본적인 설정값입니다. 처음에는 이 값만 조정해도 충분합니다.
                    * **리드타임(재발주 기간)(일)**: 발주한 상품이 입고되어 판매되어 재발주까지 걸리는 평균적인 시간(날짜). 즉 15면 15일 마다 발주를 한다는 설정 입니다.
                    * **안전재고율(%)**: 갑작스러운 주문 증가에 대비해 추가로 확보할 재고의 비율. (예: 10% 설정 시, 리드타임(재발주 기간) 동안 팔릴 양의 10%를 추가로 확보)
                    * **가산율(%)**: 계산된 발주량에 추가로 더할 여유분의 비율.
                    * **발주단위**: 상품을 주문할 때의 최소 묶음 단위. (예: 5로 설정 시, 12개 필요 -> 15개로 발주)
                    * **제외 매출수량**: 여기서 설정한 수량 미만으로 팔린 상품은 아예 계산에서 제외합니다.

                * **`[전체]` 매입처별 기본값 설정**
                    * 특정 거래처(매입처)의 상품들에만 다른 규칙을 적용하고 싶을 때 사용합니다.
                    * **예시**: '하이온'은 배송이 유독 빨라 리드타임(재발주 기간)을 7일로 짧게 설정하고 싶을 때, 매입처를 '하이온'으로 선택하고 리드타임(재발주 기간)을 7로 저장하면 됩니다.

                * **`[개별]` 품목별 상세 설정** (결과 화면 하단)
                    * **딱 하나의 특정 상품**에 대해서만 규칙을 바꾸고 싶을 때 사용합니다. 가장 강력한 설정입니다.
                    * **예시**: '하이온 강화유리 (5매) ※지문인식 가능※ ([갤럭시 S24/S25 5G] 앞면)을 평소보다 2배는 더 쟁여둬야 할 때, 상품코드로 검색한 뒤 '안전재고율'을 높게 설정하고 저장하면, 오직 해당 상품에만 이 규칙이 적용됩니다.
                    * **설정값 일괄 관리**: 검색 버튼 옆의 **'📋 설정 다운로드'** 버튼을 클릭하면 현재 보고 있는 매입처의 모든 상품 설정값을 엑셀 파일로 다운로드할 수 있습니다.
                        * 다운로드된 파일에는 각 상품이 어떤 설정을 사용하고 있는지 구분 정보도 포함됩니다.
                        * 마스터 기본값 / 매입처별 기본값 / 개별 품목 설정 중 어느 것이 적용되고 있는지 한눈에 확인 가능합니다.
                        * 파일은 PC의 다운로드 폴더에 `매입처명_품목별설정값_날짜_시간.xlsx` 형식으로 저장됩니다.

                > **✅ 체크포인트**: 설정을 변경한 후에는 반드시 **`🚀 발주량 계산 실행`** 버튼을 다시 눌러야 변경된 설정이 결과에 반영됩니다!
                
                ---
                
                #### **4. 추가 기능: 설정값 일괄 관리하기**
                
                ##### **▶ 설정값 다운로드 기능**
                
                모든 상품의 설정값을 한 번에 확인하고 관리하고 싶을 때 사용하는 기능입니다.
                
                1.  **위치**: **[개별] 품목별 상세 설정** 섹션의 검색 버튼 옆에 있는 **'📋 설정 다운로드'** 버튼
                2.  **기능**: 현재 선택된 매입처 필터의 모든 품목 설정값을 엑셀 파일로 다운로드
                3.  **다운로드되는 정보**:
                    * 상품코드
                    * 리드타임(재발주기간), 안전재고율, 가산율, 발주단위, 제외매출수량
                    * **설정구분**: 각 상품이 어떤 설정을 사용하고 있는지 표시
                        * **마스터 기본값**: 시스템 전체 기본값 사용
                        * **매입처별 기본값**: 해당 매입처 전용 설정 사용
                        * **개별 품목 설정**: 해당 상품만의 특별 설정 사용
                
                ##### **▶ 다운로드 파일 활용법**
                
                1.  **설정 현황 파악**: 어떤 상품들이 개별 설정되어 있는지 한눈에 확인
                2.  **설정 일관성 검토**: 같은 매입처 상품들이 일관된 설정을 사용하고 있는지 확인
                3.  **백업 및 문서화**: 현재 설정값들을 백업하거나 보고서 작성 시 활용
                4.  **파일명**: `매입처명_품목별설정값_20250626_174931.xlsx` 형식으로 자동 생성
                5.  **저장 위치**: PC의 다운로드 폴더에 자동 저장
                
                > **💡 팁**: 전체 매입처를 선택하면 모든 상품의 설정값을, 특정 매입처를 선택하면 해당 매입처 상품들만의 설정값을 다운로드할 수 있습니다!
                """)
            show_user_manual()

if 'settings' not in st.session_state: st.session_state.settings = load_settings()
if 'suppliers' not in st.session_state: st.session_state.suppliers = []
if 'result_df' not in st.session_state: st.session_state.result_df = pd.DataFrame()
if 'searched_item' not in st.session_state: st.session_state.searched_item = None

with st.expander("1. 분석 대상 파일 및 기간 설정", expanded=True):
    downloads_path = Path.home() / "Downloads"

    info_text_part1 = f"파일 검색 패턴: `{FILE_PATTERN}` (다운로드 폴더에서 찾습니다)"
    info_text_part2 = "▶ [상품별 매출 현황] 다운로드 엑셀 파일에는 '상품코드', '상품명', '규격', '바코드', '매출수량', '현구매단가', '현재고', '매입처' 컬럼이 포함되어야 합니다."
    st.markdown(f"{info_text_part1}<br><span style='color:blue;'>{info_text_part2}</span>", unsafe_allow_html=True)

    target_file_path = None
    manual_upload = st.toggle("수동으로 파일 업로드")
    if manual_upload:
        uploaded_file = st.file_uploader("엑셀 파일을 직접 업로드하세요.", type=['xlsx', 'xls'])
        if uploaded_file: target_file_path = uploaded_file
    else:
        latest_file = find_latest_file(downloads_path, FILE_PATTERN)
        if latest_file:
            st.success(f"✅ 자동으로 찾은 최신 파일: `{latest_file.name}`")
            target_file_path = latest_file
        else:
            st.warning(f"`{downloads_path}`에서 `{FILE_PATTERN}` 파일을 찾을 수 없습니다.")

    st.divider()
    today = datetime.date.today()
    
    date_cols = st.columns(2)
    with date_cols[0]:
        start_date = st.date_input("시작일", value=today - datetime.timedelta(days=30))
    with date_cols[1]:
        end_date = st.date_input("종료일", value=today)

    period_days = 0
    if start_date and end_date and start_date <= end_date:
        period_days = (end_date - start_date).days + 1
        st.info(f"분석 기간은 총 {period_days}일 입니다.")
    else:
        st.error("기간 설정이 올바르지 않습니다.")

if target_file_path:
    try:
        df_for_suppliers = pd.read_excel(target_file_path)
        if COL_SUPPLIER in df_for_suppliers.columns:
            unique_suppliers = sorted([str(s) for s in df_for_suppliers[COL_SUPPLIER].unique() if str(s) != 'nan'])
            st.session_state.suppliers = unique_suppliers
    except Exception:
        st.session_state.suppliers = []

with st.expander("2. 발주 설정 관리"):
    with st.container():
        st.markdown("##### [마스터] 시스템 전체 기본값 설정")
        master_defaults = st.session_state.settings.get("master_defaults", INITIAL_DEFAULT_SETTINGS)
        master_cols = st.columns(5)
        new_master_lead_time = master_cols[0].number_input("리드타임(재발주 기간)(일)", min_value=0, value=master_defaults.get('lead_time'), key="master_lt")
        new_master_safety_rate = master_cols[1].number_input("안전재고율(%)", min_value=0, value=master_defaults.get('safety_stock_rate'), key="master_sr")
        new_master_addition_rate = master_cols[2].number_input("가산율(%)", min_value=0, value=master_defaults.get('addition_rate'), key="master_ar")
        new_master_order_unit = master_cols[3].number_input("발주단위", min_value=1, value=master_defaults.get('order_unit'), key="master_ou")
        new_master_min_sales = master_cols[4].number_input("제외 매출수량", min_value=0, value=master_defaults.get('min_sales', 0), key="master_ms")

        if st.button("마스터 기본값 저장", key="master_save"):
            st.session_state.settings["master_defaults"] = {
                'lead_time': new_master_lead_time, 'safety_stock_rate': new_master_safety_rate,
                'addition_rate': new_master_addition_rate, 'order_unit': new_master_order_unit,
                'min_sales': new_master_min_sales
            }
            save_settings(st.session_state.settings)
            st.success("시스템 전체 기본값이 저장되었습니다.")
        st.caption("이곳의 값은 개별 매입처나 개별 상품에 설정 값이 정의 되지 않았거나, 새로 추가되는 매입처의 초기 설정값으로 사용됩니다.")
    st.divider()
    st.markdown("##### [전체] 매입처별 기본값 설정")
    supplier_to_edit = st.selectbox("설정할 매입처 선택", [""] + st.session_state.suppliers, key="default_selector")
    if supplier_to_edit:
        master_defaults = st.session_state.settings.get("master_defaults", INITIAL_DEFAULT_SETTINGS)
        current_defaults = st.session_state.settings["defaults"].get(supplier_to_edit, master_defaults)
        col1, col2, col3, col4, col5 = st.columns(5)
        lead_time = col1.number_input("리드타임(재발주 기간)(일)", min_value=0, value=current_defaults.get('lead_time'), key=f"d_lt_{supplier_to_edit}")
        safety_stock_rate = col2.number_input("안전재고율(%)", min_value=0, value=current_defaults.get('safety_stock_rate'), key=f"d_sr_{supplier_to_edit}")
        addition_rate = col3.number_input("가산율(%)", min_value=0, value=current_defaults.get('addition_rate'), key=f"d_ar_{supplier_to_edit}")
        order_unit = col4.number_input("발주단위", min_value=1, value=current_defaults.get('order_unit'), key=f"d_ou_{supplier_to_edit}")
        min_sales = col5.number_input("제외 매출수량", min_value=0, value=current_defaults.get('min_sales', master_defaults.get('min_sales', 0)), key=f"d_ms_{supplier_to_edit}")

        btn_col1, btn_col2, _ = st.columns([1,1,4])
        if btn_col1.button("저장", key=f"d_save_{supplier_to_edit}"):
            st.session_state.settings["defaults"][supplier_to_edit] = {
                'lead_time': lead_time, 'safety_stock_rate': safety_stock_rate,
                'addition_rate': addition_rate, 'order_unit': order_unit, 'min_sales': min_sales
            }
            save_settings(st.session_state.settings)
            st.success(f"'{supplier_to_edit}'의 기본 설정이 저장되었습니다.")
        
        if btn_col2.button("기본값으로 복원", key=f"d_reset_{supplier_to_edit}"):
            if supplier_to_edit in st.session_state.settings["defaults"]:
                del st.session_state.settings["defaults"][supplier_to_edit]
                save_settings(st.session_state.settings)
                st.success(f"'{supplier_to_edit}'의 설정이 삭제되었습니다. 마스터 기본값이 적용됩니다.")
                st.rerun()
    st.divider()
    st.markdown("##### 저장된 전체 기본 설정 목록")
    if st.session_state.settings["defaults"]:
        for i, (supplier, settings) in enumerate(st.session_state.settings["defaults"].items(), 1):
            settings_str = (
                f"리드타임(재발주 기간): {settings.get('lead_time',0)}일 &nbsp;|&nbsp; "
                f"안전재고율: {settings.get('safety_stock_rate',0)}% &nbsp;|&nbsp; "
                f"가산율: {settings.get('addition_rate',0)}% &nbsp;|&nbsp; "
                f"발주단위: {settings.get('order_unit', 1)}개 &nbsp;|&nbsp; "
                f"제외 매출수량: {settings.get('min_sales', '미설정')}개"
            )
            st.markdown(f"**{i}. {supplier}** &nbsp;|&nbsp; {settings_str}")

st.header("🚀 계산 실행")
if st.button("발주량 계산 실행", type="primary"):
    st.session_state.searched_item = None
    if target_file_path and period_days > 0:
        with st.spinner('데이터를 분석하고 있습니다...'):
            try:
                df = pd.read_excel(target_file_path)
                
                # 상품코드를 문자열로 통일하여 데이터 타입 불일치 문제 예방
                if COL_ITEM_CODE in df.columns:
                    df[COL_ITEM_CODE] = df[COL_ITEM_CODE].astype(str)

                numeric_cols_to_clean = [COL_UNIT_PRICE, COL_SALES, COL_STOCK]
                for col in numeric_cols_to_clean:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0).astype('int64')

                original_item_count = len(df)
                exclude_pattern = '|'.join(EXCLUDE_KEYWORDS)
                df_filtered = df[~df[COL_ITEM_NAME].astype(str).str.contains(exclude_pattern, na=False)].copy()
                keyword_excluded_count = original_item_count - len(df_filtered)

                df_filtered['min_sales_applied'] = df_filtered.apply(get_min_sales_for_row, axis=1, settings=st.session_state.settings)
                df_final_filtered = df_filtered[df_filtered[COL_SALES] >= df_filtered['min_sales_applied']].copy()
                df_final_filtered.drop(columns=['min_sales_applied'], inplace=True)

                sales_excluded_count = len(df_filtered) - len(df_final_filtered)
                st.info(f"총 {original_item_count}개 품목 중, 키워드로 {keyword_excluded_count}개, 매출수량 기준으로 {sales_excluded_count}개를 제외하고 계산합니다.")

                required_cols = [COL_ITEM_CODE, COL_ITEM_NAME, COL_UNIT_PRICE, COL_SUPPLIER, COL_SALES, COL_STOCK]
                if not all(col in df.columns for col in required_cols):
                    missing_cols = [col for col in required_cols if col not in df.columns]
                    st.error(f"엑셀 파일에 필수 컬럼이 없습니다: {', '.join(missing_cols)}")
                else:
                    result_df = calculate_order_quantity(df_final_filtered, st.session_state.settings, period_days)
                    st.session_state.result_df = result_df
                    st.success("발주량 계산이 완료되었습니다.")
            except Exception as e:
                st.error(f"파일 처리 또는 계산 중 오류 발생: {e}")
                st.session_state.result_df = pd.DataFrame()

if not st.session_state.result_df.empty:
    result_df = st.session_state.result_df.copy()
    if COL_SPEC in result_df.columns:
        result_df['상품명 (규격)'] = result_df[COL_ITEM_NAME].astype(str) + result_df[COL_SPEC].apply(lambda x: f' ({x})' if pd.notna(x) and str(x).strip() != '' else '')
    else:
        result_df['상품명 (규격)'] = result_df[COL_ITEM_NAME]
    st.header("📊 요약 대시보드 및 결과 데이터")
    all_suppliers_from_result = sorted(result_df[COL_SUPPLIER].unique())
    
    view_option = st.radio("데이터 필터", ["전체"] + all_suppliers_from_result, horizontal=True, key="data_filter_radio")
    
    if 'previous_view_option' not in st.session_state:
        st.session_state.previous_view_option = "전체"
    
    if st.session_state.previous_view_option != view_option:
        st.session_state.searched_item = None
        if 'search_code_input' in st.session_state:
            st.session_state.search_code_input = ""
        st.session_state.previous_view_option = view_option
        st.rerun()

    df_for_view = result_df
    dashboard_title_prefix = "전체"
    if view_option != "전체":
        df_for_view = result_df[result_df[COL_SUPPLIER] == view_option]
        dashboard_title_prefix = view_option

    order_needed_df = df_for_view[df_for_view['추천 발주량'] > 0].copy()

    if not order_needed_df.empty:
        total_items = len(order_needed_df)
        total_quantity = order_needed_df['추천 발주량'].sum()
        order_needed_df.loc[:, '예상 발주 금액'] = order_needed_df['추천 발주량'] * order_needed_df[COL_UNIT_PRICE]
        total_cost = order_needed_df['예상 발주 금액'].sum()
        kpi_cols = st.columns(3)
        kpi_cols[0].metric(f"[{dashboard_title_prefix}] 추천 품목 수", f"{total_items} 개")
        kpi_cols[1].metric(f"[{dashboard_title_prefix}] 추천 수량", f"{total_quantity:,.0f} 개")
        kpi_cols[2].metric(f"[{dashboard_title_prefix}] 예상 금액", f"₩ {total_cost:,.0f}")

    st.divider()
    
    urgent_order_df = df_for_view[df_for_view['비고'] == '발주 필요 (긴급)'].copy()
    if not urgent_order_df.empty:
        display_ratio = st.slider("표시할 긴급 발주 품목 비율 (%)", min_value=10, max_value=100, value=25, step=5)
        num_to_show = math.ceil(len(urgent_order_df) * (display_ratio / 100))
        if num_to_show < 1: num_to_show = 1
        
        graph_data = urgent_order_df.nlargest(num_to_show, '추천 발주량')
        st.subheader(f"[{dashboard_title_prefix}] 긴급 발주 Top {num_to_show}개 (추천량 순)")
        fig = px.bar(graph_data, x='상품명 (규격)', y='추천 발주량', 
                     hover_data=[COL_ITEM_CODE, COL_BARCODE, '현재고', '재고 소진 예상일'],
                     labels={'추천 발주량': '추천 발주 수량', '상품명 (규격)': '상품명'})
        st.plotly_chart(fig, use_container_width=True)

    st.divider()
    
    st.header("📑 발주 추천 상품")
    st.caption("추천 발주량이 0보다 큰 품목만 표시됩니다.")
    
    display_columns_order = [
        COL_ITEM_CODE, '상품명 (규격)', COL_BARCODE, COL_STOCK, COL_SALES,
        '재고 소진 예상일', '추천 발주량', '비고', '적용된 설정',
        COL_UNIT_PRICE, '예상 발주 금액', COL_SUPPLIER
    ]
    final_display_columns = [col for col in display_columns_order if col in order_needed_df.columns]
    
    if not order_needed_df.empty:
        df_to_display_main = order_needed_df[final_display_columns]
        
        st.dataframe(df_to_display_main.style.format(formatter={
            COL_STOCK: "{:,.0f}", COL_SALES: "{:,.0f}", '추천 발주량': "{:,.0f}",
            COL_UNIT_PRICE: "₩{:,.0f}", '예상 발주 금액': "₩{:,.0f}", '재고 소진 예상일': "{:.0f}"
        }, na_rep='').map(style_remarks, subset=['비고']), use_container_width=True, hide_index=True, height=735)

        st.markdown("<hr style='margin:0.5rem 0; border-top: 2px solid #ccc;'>", unsafe_allow_html=True)
        total_cols = st.columns(len(final_display_columns))
        
        item_count = len(df_to_display_main)
        sum_stock = df_to_display_main[COL_STOCK].sum()
        sum_sales = df_to_display_main[COL_SALES].sum()
        sum_order_qty = df_to_display_main['추천 발주량'].sum()
        sum_order_cost = df_to_display_main.get('예상 발주 금액', pd.Series(0)).sum()
        
        total_cols[0].markdown(f"<div class='total-cell' style='text-align: left;'>합계 ({item_count}개 품목)</div>", unsafe_allow_html=True)
        if COL_STOCK in final_display_columns: total_cols[final_display_columns.index(COL_STOCK)].markdown(f"<div class='total-cell'>{sum_stock:,.0f}</div>", unsafe_allow_html=True)
        if COL_SALES in final_display_columns: total_cols[final_display_columns.index(COL_SALES)].markdown(f"<div class='total-cell'>{sum_sales:,.0f}</div>", unsafe_allow_html=True)
        if '추천 발주량' in final_display_columns: total_cols[final_display_columns.index('추천 발주량')].markdown(f"<div class='total-cell'>{sum_order_qty:,.0f}</div>", unsafe_allow_html=True)
        if '예상 발주 금액' in final_display_columns: total_cols[final_display_columns.index('예상 발주 금액')].markdown(f"<div class='total-cell'>₩ {sum_order_cost:,.0f}</div>", unsafe_allow_html=True)

        output = BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df_to_display_main.to_excel(writer, index=False, sheet_name='OrderList')
            for column in df_to_display_main:
                column_length = max(df_to_display_main[column].astype(str).map(len).max(), len(column))
                col_idx = df_to_display_main.columns.get_loc(column)
                writer.sheets['OrderList'].set_column(col_idx, col_idx, column_length + 2)
        st.download_button(label="📥 엑셀 다운로드", data=output.getvalue(), file_name=f"발주추천결과_{datetime.datetime.now().strftime('%Y%m%d')}.xlsx")

    st.divider()
    
    with st.expander("⚙️ [개별] 품목별 상세 설정 (기본값 덮어쓰기)"):
        st.markdown("##### `발주량 계산 실행` 후, 이곳에서 특정 품목의 설정만 개별적으로 변경할 수 있습니다.")
        search_col1, search_col2, search_col3 = st.columns([2.5, 0.7, 0.8])
        with search_col1:
            search_code = st.text_input("설정할 상품코드 검색", placeholder="상품코드를 입력하고 검색 버튼을 누르세요.", key="search_code_input")
        with search_col2:
            st.write("") 
            if st.button("🔍 검색"):
                search_result = df_for_view[df_for_view[COL_ITEM_CODE].astype(str) == search_code]
                if not search_result.empty:
                    st.session_state.searched_item = search_result.iloc[0].to_dict()
                else:
                    st.error(f"현재 선택된 '{dashboard_title_prefix}' 필터 내에 해당 상품코드가 없습니다.")
                    st.session_state.searched_item = None
        
        with search_col3:
            st.write("")
            # 설정값 엑셀 다운로드 버튼 추가
            settings_export_data = create_settings_export_data(df_for_view, st.session_state.settings)
            if not settings_export_data.empty:
                settings_output = BytesIO()
                with pd.ExcelWriter(settings_output, engine='xlsxwriter') as writer:
                    settings_export_data.to_excel(writer, index=False, sheet_name='ItemSettings')
                    # 컬럼 너비 자동 조정
                    for column in settings_export_data:
                        column_length = max(settings_export_data[column].astype(str).map(len).max(), len(column))
                        col_idx = settings_export_data.columns.get_loc(column)
                        writer.sheets['ItemSettings'].set_column(col_idx, col_idx, column_length + 2)
                
                if st.download_button(
                    label="📋 설정 다운로드",
                    data=settings_output.getvalue(),
                    file_name=f"{dashboard_title_prefix}_품목별설정값_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                    key="settings_download_btn"
                ):
                    st.toast(f"✅ {dashboard_title_prefix} 전체 품목 설정값이 다운로드 폴더에 저장되었습니다!", icon='✅')
        
        if st.session_state.searched_item:
            item_data = st.session_state.searched_item
            item_code_to_edit = str(item_data[COL_ITEM_CODE])
            supplier = item_data[COL_SUPPLIER]
            
            master_defaults = st.session_state.settings.get("master_defaults", INITIAL_DEFAULT_SETTINGS)
            supplier_defaults = st.session_state.settings["defaults"].get(supplier, master_defaults)
            override_settings = st.session_state.settings["overrides"].get(item_code_to_edit, {})
            final_display_settings = {**supplier_defaults, **override_settings}

            st.success(f"**'{item_data['상품명 (규격)']}'** 품목이 선택되었습니다.")
            
            col1, col2, col3, col4, col5 = st.columns(5)
            new_lead_time = col1.number_input("리드타임(재발주 기간)(일)", min_value=0, value=final_display_settings.get('lead_time'), key=f"o_lt_{item_code_to_edit}")
            new_safety_rate = col2.number_input("안전재고율(%)", min_value=0, value=final_display_settings.get('safety_stock_rate'), key=f"o_sr_{item_code_to_edit}")
            new_addition_rate = col3.number_input("가산율(%)", min_value=0, value=final_display_settings.get('addition_rate'), key=f"o_ar_{item_code_to_edit}")
            new_order_unit = col4.number_input("발주단위", min_value=1, value=final_display_settings.get('order_unit'), key=f"o_ou_{item_code_to_edit}")
            new_min_sales = col5.number_input("제외 매출수량", min_value=0, value=final_display_settings.get('min_sales', master_defaults.get('min_sales',0)), key=f"o_ms_{item_code_to_edit}")
            
            btn_col1, btn_col2, _ = st.columns([1,1,4])
            if btn_col1.button("개별 설정 저장", key=f"o_save_{item_code_to_edit}"):
                st.session_state.settings["overrides"][item_code_to_edit] = {
                    'lead_time': new_lead_time, 'safety_stock_rate': new_safety_rate,
                    'addition_rate': new_addition_rate, 'order_unit': new_order_unit, 'min_sales': new_min_sales
                }
                save_settings(st.session_state.settings)
                # FIX: 파일 저장 후 즉시 설정을 다시 로드하여 상태를 동기화
                st.session_state.settings = load_settings()
                st.success("개별 설정이 저장되었습니다. '발주량 계산 실행'을 다시 눌러주세요.")
                st.session_state.searched_item = None
                # FIX: 명시적으로 앱을 다시 실행하여 변경사항을 모든 컴포넌트에 즉시 반영
                st.rerun()

            if btn_col2.button("기본값으로 복원", key=f"o_reset_{item_code_to_edit}"):
                if item_code_to_edit in st.session_state.settings["overrides"]:
                    del st.session_state.settings["overrides"][item_code_to_edit]
                    save_settings(st.session_state.settings)
                    # FIX: 파일 저장 후 즉시 설정을 다시 로드하여 상태를 동기화
                    st.session_state.settings = load_settings()
                    st.success("개별 설정이 삭제되었습니다. '발주량 계산 실행'을 다시 눌러주세요.")
                    st.session_state.searched_item = None
                    # FIX: 명시적으로 앱을 다시 실행하여 변경사항을 모든 컴포넌트에 즉시 반영
                    st.rerun()
        
        st.divider()
        st.markdown(f"##### 저장된 품목별 개별 설정 목록 ([{dashboard_title_prefix}] 필터 적용됨)")
        overrides = st.session_state.settings.get("overrides", {})
        if overrides:
            override_item_codes = list(overrides.keys())
            override_df = df_for_view[df_for_view[COL_ITEM_CODE].isin(override_item_codes)]
            
            if not override_df.empty:
                for i, row in enumerate(override_df.to_dict('records'), 1):
                    code = str(row[COL_ITEM_CODE])
                    item_name_str = f" ({row['상품명 (규격)']})"
                    settings = overrides[code]
                    
                    settings_str_parts = []
                    if 'lead_time' in settings: settings_str_parts.append(f"리드타임(재발주 기간): {settings['lead_time']}일")
                    if 'safety_stock_rate' in settings: settings_str_parts.append(f"안전재고율: {settings['safety_stock_rate']}%")
                    if 'addition_rate' in settings: settings_str_parts.append(f"가산율: {settings['addition_rate']}%")
                    if 'order_unit' in settings: settings_str_parts.append(f"발주단위: {settings['order_unit']}개")
                    if 'min_sales' in settings: settings_str_parts.append(f"제외 매출수량: {settings['min_sales']}개")
                    
                    st.markdown(f"**{i}. {code}{item_name_str}** &nbsp;|&nbsp; " + " &nbsp;|&nbsp; ".join(settings_str_parts))

    st.divider()
    
    st.header("📦 초과재고 현황")
    overstock_df = df_for_view[df_for_view['비고'].isin(['초과재고', '악성 초과재고'])].copy()
    
    if not overstock_df.empty:
        overstock_df.loc[:, '초과재고 비율 (재고/매출)'] = overstock_df[COL_STOCK] / overstock_df[COL_SALES].replace(0, np.nan)
        median_ratio = overstock_df['초과재고 비율 (재고/매출)'].median()
        if pd.notna(median_ratio):
            malignant_rows_mask = overstock_df['초과재고 비율 (재고/매출)'] >= median_ratio
            overstock_df.loc[:, '비고'] = np.where(malignant_rows_mask, "악성 초과재고", "초과재고")
        overstock_df.loc[:, '초과재고 금액'] = overstock_df['초과재고 수량'] * overstock_df[COL_UNIT_PRICE]
        
        overstock_display_cols_order = [
            COL_ITEM_CODE, '상품명 (규격)', COL_BARCODE, COL_STOCK, '초과재고 수량', COL_SALES, 
            '재고 소진 예상일', '초과재고 비율 (재고/매출)', COL_UNIT_PRICE, '초과재고 금액', '비고', COL_SUPPLIER
        ]
        final_overstock_cols = [col for col in overstock_display_cols_order if col in overstock_df.columns]
        df_to_display_overstock = overstock_df[final_overstock_cols]
        
        st.dataframe(df_to_display_overstock.style.format(formatter={
            COL_STOCK: "{:,.0f}", '초과재고 수량': "{:,.0f}", COL_SALES: "{:,.0f}", 
            '재고 소진 예상일': "{:.0f}", '초과재고 비율 (재고/매출)': "{:.1f} 배",
            COL_UNIT_PRICE: "₩{:,.0f}", '초과재고 금액': "₩{:,.0f}"
        }, na_rep='').map(style_remarks, subset=['비고']), use_container_width=True, hide_index=True, height=735)

        st.markdown("<hr style='margin:0.5rem 0; border-top: 2px solid #ccc;'>", unsafe_allow_html=True)
        overstock_total_cols = st.columns(len(final_overstock_cols))
        
        overstock_item_count = len(df_to_display_overstock)
        overstock_sum_stock = df_to_display_overstock[COL_STOCK].sum()
        overstock_sum_over_qty = df_to_display_overstock['초과재고 수량'].sum()
        overstock_sum_sales = df_to_display_overstock[COL_SALES].sum()
        overstock_sum_over_cost = df_to_display_overstock.get('초과재고 금액', pd.Series(0)).sum()
        
        overstock_total_cols[0].markdown(f"<div class='total-cell' style='text-align: left;'>합계 ({overstock_item_count}개 품목)</div>", unsafe_allow_html=True)
        if COL_STOCK in final_overstock_cols: overstock_total_cols[final_overstock_cols.index(COL_STOCK)].markdown(f"<div class='total-cell'>{overstock_sum_stock:,.0f}</div>", unsafe_allow_html=True)
        if '초과재고 수량' in final_overstock_cols: overstock_total_cols[final_overstock_cols.index('초과재고 수량')].markdown(f"<div class='total-cell'>{overstock_sum_over_qty:,.0f}</div>", unsafe_allow_html=True)
        if COL_SALES in final_overstock_cols: overstock_total_cols[final_overstock_cols.index(COL_SALES)].markdown(f"<div class='total-cell'>{overstock_sum_sales:,.0f}</div>", unsafe_allow_html=True)
        if '초과재고 금액' in final_overstock_cols: overstock_total_cols[final_overstock_cols.index('초과재고 금액')].markdown(f"<div class='total-cell'>₩ {overstock_sum_over_cost:,.0f}</div>", unsafe_allow_html=True)

        overstock_output = BytesIO()
        with pd.ExcelWriter(overstock_output, engine='xlsxwriter') as writer:
            df_to_display_overstock.to_excel(writer, index=False, sheet_name='Overstock')
            for column in df_to_display_overstock:
                column_length = max(df_to_display_overstock[column].astype(str).map(len).max(), len(column))
                col_idx = df_to_display_overstock.columns.get_loc(column)
                writer.sheets['Overstock'].set_column(col_idx, col_idx, column_length + 2)
        
        st.download_button(label="📥 초과재고 현황 엑셀 다운로드", data=overstock_output.getvalue(), file_name=f"초과재고현황_{datetime.datetime.now().strftime('%Y%m%d')}.xlsx")
    else:
        st.info(f"'{dashboard_title_prefix}'에서 초과재고로 분류된 품목이 없습니다.")