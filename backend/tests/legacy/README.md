# Legacy tests

Questa cartella contiene test iterativi storici (bug-fix specifici di
iterazioni passate). Sono stati spostati qui per **ridurre il rumore**
nella cartella `tests/` principale, che ora ospita solo i test funzionali
attivi sulle feature core.

I test qui presenti continuano a essere validi (in linea di principio)
ma non sono più eseguiti automaticamente da `pytest` per default:
sono presenti come **documentazione storica** dei bug risolti e come
regression-suite opzionale.

## Come eseguirli comunque

```bash
cd /app/backend
python -m pytest tests/legacy/ -v
```

## Convenzione

Un test è considerato **legacy** se:
- Il suo nome contiene `iter<numero>` (`test_iter117_bugfixes.py`, ecc.).
- Riguarda un fix puntuale non più critico per la correttezza della logica
  di business odierna.
- La feature testata è stabilizzata da almeno 3 iterazioni.
