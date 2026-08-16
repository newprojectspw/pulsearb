# Fixtures

Dados **reais** colhidos da produção em 2026-08-16 (verificação ao vivo do
Paulo, via Colab/IP dos EUA) — ver `docs/API_NOTES.md` seção 12.

| Arquivo | Origem | Integridade |
|---|---|---|
| `clob_market_compact.json` | `GET https://clob.polymarket.com/clob-markets/{conditionId}` | **Íntegro** (anexo A2, verbatim) |
| `gamma_market_btc_updown_5m.json` | `GET https://gamma-api.polymarket.com/markets/slug/btc-updown-5m-1786891500` | **Reconstruído** do anexo A1: a resposta original foi truncada; os campos aqui presentes são os capturados, com os nomes camelCase da Gamma. `description` é representativa da regra TWAP capturada, não verbatim. Este mercado **não tem `feeSchedule`** — o que exercita o gate de fee da descoberta. |
| `gamma_market_with_feeschedule.json` | Segundo mercado do mesmo tipo citado no anexo A1 | **Parcial**: só os campos de fee/rewards capturados, mais o mínimo estrutural para parsear. |
| `gamma_market_zombie.json` | Padrão de mercado-zumbi observado ao vivo (API_NOTES 12.12) | **Sintético estrutural** fiel ao padrão real: janela de dez/2025 com `closed=false`. Teste negativo obrigatório do filtro anti-zumbi. |
| `gamma_market_hourly_current.json` | Anexo A3 — janela horária **atual**, colhida ao vivo | **Parcial**: campos capturados (slug, question, conditionId, endDate, resolutionSource, tick, min, spread, liquidez, volume, `umaReward`, `feeSchedule`) mais o mínimo estrutural (`clobTokenIds`, `startDate`) derivado do padrão. |
| `gamma_market_stale_slug_resolution.json` | Caso real: `bitcoin-up-or-down-august-16-2pm-et` (SEM ano) resolvendo 200 na janela homônima de 2025 (API_NOTES 12.12b) | **Parcial**: os campos que denunciam o mercado antigo são os capturados (`endDate` 2025, `feesEnabled:false`, `feeType:null`, tick 0.001); o resto é estrutura. Par negativo do `hourly_current`. |
| `gamma_market_hourly_binance.json` | Janela de 1h (API_NOTES 12.2/12.2b) | **Sintético estrutural** sobre o padrão de slug informado. |

Nota: `startDate`/`endDate` das duas fixtures Gamma positivas são o mínimo
estrutural derivado do epoch do slug (grade alinhada, 12.1) — a captura
original foi truncada antes desses campos.
| `rtds_*.json` | Formato dos eventos do RTDS conforme o protocolo verificado no SDK oficial (API_NOTES seções 6.2 e 12.3) | **Sintético estrutural** — payloads montados a partir do protocolo verificado, não capturas de rede. Substituir por capturas reais na primeira rodada do recorder. |
| `clob_ws_book.json` | Formato dos eventos do WS de mercado do CLOB | **Sintético estrutural** — idem. |

Regra: fixture sintética nunca vira "prova" de comportamento do servidor.
Elas provam apenas que o NOSSO parser aceita o formato documentado. A prova
real vem das capturas do recorder (M1.C) e dos smokes (M1.E).
