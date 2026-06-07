"""Utilidades para manejo seguro de modelos."""

def safe_save_model(out_path, result, min_cagr_improvement=0.5):
    """Solo guarda si el nuevo modelo supera al actual por al menos min_cagr_improvement puntos."""
    import json
    from pathlib import Path
    out_path = Path(out_path)
    
    new_cagr = result.get('metrics_oos', result.get('metrics_is', {})).get('cagr', 0)
    
    # Leer el actual
    prev_cagr = 0.0
    if out_path.exists():
        try:
            prev = json.load(open(out_path))
            pm = prev.get('metrics_oos', prev.get('metrics_is', {}))
            prev_cagr = pm.get('cagr', 0.0)
        except: pass
    
    if new_cagr > prev_cagr + min_cagr_improvement:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, 'w') as f:
            json.dump(result, f, indent=2)
        return True, prev_cagr, new_cagr
    return False, prev_cagr, new_cagr
