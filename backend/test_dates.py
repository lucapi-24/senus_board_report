import asyncio
from datetime import date
from app.services import period_detector
import pdfplumber

async def run_tests():
    pdf_path = '/app/test_user_pdf.pdf'
    with pdfplumber.open(pdf_path) as pdf:
        pages = [(i+1, page.extract_text() or '') for i, page in enumerate(pdf.pages)]

    print('=== Probando con tu PDF del root ===')
    print()

    # Test HY2025 range (01/07/2024 - 31/12/2024)
    ok, msg, _ = await period_detector.validate_against('HY2025', date(2024,7,1), date(2024,12,31), pages)
    print('Rango HY2025 (01/07/2024 - 31/12/2024): ' + ('ACEPTADO' if ok else 'RECHAZADO'))
    print('  Mensaje: ' + msg)
    print()

    # Test HY2026 range (01/07/2025 - 31/12/2025)
    ok, msg, _ = await period_detector.validate_against('HY2026', date(2025,7,1), date(2025,12,31), pages)
    print('Rango HY2026 (01/07/2025 - 31/12/2025): ' + ('ACEPTADO' if ok else 'RECHAZADO'))
    print('  Mensaje: ' + msg)
    print()

    # Show all detected dates
    print('Todas las fechas detectadas:')
    cands = period_detector.detect_periods(pages)
    for c in cands:
        if c.end_date:
            print('  ' + str(c.label or 'sin label') + ': ' + str(c.start_date) + ' a ' + str(c.end_date) + ' (score=' + str(round(c.score,1)) + ')')

asyncio.run(run_tests())
