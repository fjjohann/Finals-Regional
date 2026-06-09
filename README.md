# Finals Regional

Painel para leitura de rankings regionais de Beach Tennis via web scraping.

## Estrutura

- `scripts/scrape_rankings.py`: coleta os rankings das páginas da FPT.
- `data/sources.json`: definição das regionais e categorias monitoradas.
- `docs/index.html`: painel estático.
- `docs/data/rankings.json`: base gerada pelo scraper para o painel.

## Atualizar dados

```bash
python3 scripts/scrape_rankings.py
```

O workflow em `.github/workflows/update-rankings.yml` também permite atualizar os dados pelo GitHub Actions. No painel publicado, o botão **Atualizar rankings** abre esse workflow para execução manual pela sessão logada no GitHub.

## Publicação

O workflow `.github/workflows/deploy-pages.yml` publica a pasta `docs/` no GitHub Pages sempre que houver push na branch `main`. Quando o workflow de atualização dos rankings gera commit com dados novos, o deploy do painel é acionado na sequência.
