from trend_scalper.config import load_settings, validate_settings


settings = load_settings()
errors = validate_settings(settings)

print(f"mode={settings.trading_mode} symbol={settings.symbol} timeframe={settings.timeframe}")
if errors:
    for error in errors:
        print(f"warning: {error}")
else:
    print("configuration ok")
