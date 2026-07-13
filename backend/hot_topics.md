# TEMI CALDI DA MONITORARE — POPULUS

Documento aggiornabile in qualsiasi momento dal programmatore per adattare il
comportamento della IA generatrice di faide ai trend correnti.

**IMPORTANTE**: la IA legge questo file ad ogni ciclo di generazione. Modifiche
qui hanno effetto immediato SENZA riavviare il backend. Aggiungere/rimuovere
righe sotto "Argomenti prioritari". Le righe vuote e i commenti (`#`) sono
ignorati.

## Criteri di priorità (invariati)

Uno o più dei seguenti fattori aumentano la probabilità che una notizia venga
scelta per generare una faida:

- **Due fazioni chiare** (persone, gruppi, tifoserie, correnti).
- **Emozione forte** (rabbia, indignazione, ironia, gossip, tifo).
- **Conflitto pubblico** (litigi, gaffe, dichiarazioni divisive).
- **Personaggi riconoscibili** (già noti al pubblico italiano).
- **Opinioni contrapposte** (nessun consenso maggioritario).
- **Controversia elevata** (già virale sui social o oggetto di dibattito).

## Argomenti prioritari

Se una notizia riguarda uno dei seguenti argomenti, dagli un boost significativo
di engagement e considera prioritaria la sua selezione (a parità di altri
criteri, scegli l'argomento in lista):

- Sanremo
- Grande Fratello
- Temptation Island
- Amici
- Calcio (partite, calciomercato, VAR)
- Cronaca nera
- Gossip / influencer / rapper
- Casi TV (litigi in diretta, gaffe, dichiarazioni provocatorie)
- Gaffe e scivoloni di personaggi pubblici
- Politica economica (tasse, manovre, riforme che scatenano dibattito)
- Notizie polarizzanti di politica (leader in scontro, decisioni divisive)
- Episodi di violenza (fatti gravi che scuotono l'opinione pubblica)
- Guerra (conflitti in corso, dichiarazioni geopolitiche esplosive)
- Pandemie (emergenze sanitarie con posizioni contrapposte)
- Reality e talent (in generale, non solo quelli listati sopra)
- Faide tra big player della Silicon Valley (Musk, Zuckerberg, Altman, Bezos…)
- Notizie controverse sull'intelligenza artificiale (rischi, licenziamenti,
  copyright, deepfake)
- Intelligenza artificiale e politica (regolamentazione, deepfake in campagna,
  uso governativo)
- Questioni di genere (parità, identità, discriminazioni)
- Femminicidi (con particolare attenzione al dibattito sulle cause e
  responsabilità sistemiche)

## Note per il programmatore

- Le righe che iniziano con `-` o `*` vengono estratte come voci della lista.
- Righe che iniziano con `#`, `##`, `###` sono trattate come intestazioni
  Markdown e ignorate.
- Modifica il file, salva → il prossimo tick del generatore (max 30 min) userà
  la lista aggiornata.
