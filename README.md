# Alpha Factory V15.4 (Quant Auto-Trading Bot)

S&P 500 주식 데이터를 기반으로 모멘텀과 퀄리티 팩터를 분석하여, 매일 지정된 시간에 자동으로 포트폴리오를 리밸런싱하는 파이썬 기반 알고리즘 트레이딩 봇입니다. 

## 주요 기능
- **Alpha Engine:** 60일 모멘텀(0.6) + 126일 퀄리티 스코어(0.4) 팩터 결합
- **Risk Management:** - VIX/SPY 변동성 기반 타겟 볼라틸리티(Target Volatility) 레버리지 조절
  - 일일 포트폴리오 회전율(Turnover) 35% 캡(Cap) 적용
- **Automation:** GitHub Actions를 활용한 평일 야간 완전 무인 매매 시스템 구축
- **Notification:** 매매 결과 및 현재 자산 현황 Telegram 자동 보고

## 사용 방법 (How to use)
1. 이 저장소를 Fork 합니다.
2. Alpaca 가상 계좌(Paper Trading) API Key를 발급받습니다.
3. Telegram Bot을 생성하고 Token과 Chat ID를 메모합니다.
4. Fork한 리포지토리의 `Settings > Secrets and variables > Actions`에 다음 키들을 등록합니다:
   - `ALPACA_API_KEY`
   - `ALPACA_SECRET_KEY`
   - `TELEGRAM_TOKEN`
   - `TELEGRAM_CHAT_ID`
5. `.github/workflows/main.yml`에 설정된 시간에 맞춰 봇이 자동으로 작동합니다! (기본값: 한국 시간 오전 4시)

## ⚠️ Disclaimer (면책 조항)
본 리포지토리의 코드와 전략은 학술 및 연구 목적으로만 제공되며, 어떠한 형태의 금융 조언이나 투자 권유를 의미하지 않습니다. 이 코드를 실전 투자(Real Money)에 사용하여 발생하는 모든 금전적 손실과 법적 책임은 전적으로 사용자 본인에게 있습니다. 반드시 충분한 모의투자(Paper Trading)와 백테스트를 거친 후 주의하여 사용하시기 바랍니다.
