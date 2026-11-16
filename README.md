# Polymarket MCP V2

Servidor MCP Python 3.12+ para dados públicos e trading protegido via
[`polymarket-client`](https://github.com/Polymarket/py-sdk). Por defeito usa
Streamable HTTP em `/mcp`, sem chave privada e com `DRY_RUN=true`.

## Segurança

**O servidor não implementa autenticação nem TLS.** Qualquer cliente com acesso
pode ler a carteira, cancelar ordens e, quando `DRY_RUN=false`, enviar ordens.
O Compose publica apenas em `127.0.0.1`; mantenha-o numa rede de confiança e
use um proxy autenticado com TLS para acesso remoto. Nunca exponha a porta
diretamente à Internet. Restrinja também acesso ao ficheiro `.env` e aos logs.

O modo sem chave permite apenas consultas públicas. Com chave, o SDK cria ou
deriva credenciais CLOB e assina pedidos de autenticação quando o cliente
autenticado é inicializado; `DRY_RUN` impede a assinatura e envio **de ordens**,
não estas operações de autenticação. O cliente é inicializado sob demanda.
Sem funder explícito usa a morada EOA da chave, evitando o deploy automático
da deposit wallet predefinida do SDK. Para proxy/Safe forneça uma carteira
existente em `POLYMARKET_FUNDER_ADDRESS`. Para Deposit Wallet, o servidor
confirma no relayer que já existe antes de inicializar o cliente, bloqueando
deploy automático também em simulações. Esta verificação usa internals do SDK
0.20, cuja versão minor está fixada. Consulte a documentação do SDK sobre
o tipo de carteira e os seus efeitos. `cancel_orders` é um botão de pânico real,
inclusive em `DRY_RUN`.

O SafetyGate bloqueia ordens em caso de dados ausentes ou inválidos, spread,
liquidez ou exposição fora dos limites. A exposição soma, por condition ID, o
maior entre custo e valor atual de cada posição aberta e o valor remanescente
de todas as ordens abertas. As ordens de mercado são FOK, com preço limite
derivado do livro e slippage máximo igual a `MAX_SPREAD_PCT`; vendas usam
shares calculadas pelo melhor bid para que o valor estimado não exceda o
montante em USD. Ordens limite BUY arredondam para
baixo e SELL para cima. Estes controlos são conservadores, mas não garantem
ausência de perdas: preços e posições podem mudar entre consulta e execução,
dados da Data API podem atrasar-se e operações externas ao servidor não são
serializadas. Para carteiras que não sejam Deposit Wallet, o SDK lista apenas
ordens das credenciais atuais, não as de outros signatários. Taxas não estão
incluídas nos limites. O saldo livre é uma estimativa que subtrai o valor das
ordens BUY abertas do saldo CLOB. Use uma carteira dedicada
e verifique os saldos e permissões antes de ativar trading.

## Arranque

```bash
cp .env.example .env
docker compose up --build -d
```

O endpoint é `http://127.0.0.1:8000/mcp`. Para execução local:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
polymarket-mcp
```

Para clientes stdio, defina `MCP_TRANSPORT=stdio` e execute `polymarket-mcp`.
Logs estruturados são enviados para stderr. Para mudar o porto no Docker,
altere tanto `MCP_PORT` como o mapeamento de portas do Compose.

| Variável | Default | Uso |
|---|---|---|
| `MCP_TRANSPORT` | `streamable-http` | `streamable-http` ou `stdio` |
| `MCP_HOST` / `MCP_PORT` | `0.0.0.0` / `8000` | Interface e porto HTTP |
| `LOG_LEVEL` / `LOG_FORMAT` | `INFO` / `json` | Nível e formato (`json` ou `console`) |
| `POLYMARKET_PRIVATE_KEY` | vazio | Chave de assinatura; vazio ativa read-only |
| `POLYMARKET_FUNDER_ADDRESS` | vazio | Carteira proxy/Safe existente; vazio usa EOA |
| `DRY_RUN` | `true` | Simula ordens sem as assinar nem enviar |
| `MAX_ORDER_USD` | `25.0` | Limite por ordem |
| `MAX_MARKET_EXPOSURE_USD` | `100.0` | Exposição máxima por mercado |
| `MAX_SPREAD_PCT` | `5.0` | Spread e slippage máximos, em percentagem |
| `MIN_BOOK_LIQUIDITY_USD` | `500.0` | Liquidez mínima nos cinco melhores níveis |

As ferramentas são `search_markets`, `get_market_details`, `analyze_orderbook`,
`get_price_history`, `get_portfolio_summary`, `get_open_orders`,
`get_redeemable_positions`, `place_limit_order`, `place_market_order` e
`cancel_orders`. Erros são devolvidos como objetos com `error` e `code`.

O SDK 0.x pode alterar APIs entre versões minor; o projeto fixa a série 0.20.
Para executar os testes: `pip install -e '.[test]' && pytest`.
