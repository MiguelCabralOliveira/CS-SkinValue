"""CS-SkinValue forecasting module.

Public API:
    from forecasting import forecast

    f = forecast("AK-47 | Redline (Field-Tested)", horizon=7, model="chronos")
    print(f.point)           # numpy array of predicted prices
    print(f.lower, f.upper)  # 80% confidence band
    print(f.dates)           # forecast dates
"""
from forecasting.inference import forecast, list_available_models, Forecast

__all__ = ["forecast", "list_available_models", "Forecast"]
