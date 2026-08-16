"""
Cliente HTTP da API industrial do parceiro.

O cliente é a camada mais burra da pilha, e isso é o desenho: ele faz a chamada, mede
quanto demorou e devolve o que voltou. **Não classifica** (`StatusRetorno` é decisão
determinística da fronteira MCP, T7), **não cacheia** (é do servidor MCP, T13) e **não
repete** — cada uma dessas três coisas precisa virar evento no trace, e o cliente não
tem writer nem `seq`. Um retry escondido aqui apareceria no trace como uma chamada
lenta, e a métrica de indisponibilidade viraria ruído.

O que ele faz de não-trivial é injetar o `seed` da run em toda query. Omitir o seed não
dá ambiente aleatório: dá *outro* ambiente fixo, determinístico por
`hash("noseed"|recurso|categoria)` (CENARIOS §8.1). Ou seja, esquecer o seed numa
chamada não quebra nada visivelmente — só troca o ambiente daquela chamada em silêncio,
que é a pior forma de bug possível numa bateria de avaliação.
"""

from __future__ import annotations

import time
from types import TracebackType
from typing import Any

import httpx
from pydantic import BaseModel

TIMEOUT_PADRAO_S = 10.0


class RawResponse(BaseModel):
    """O que voltou da API, sem interpretação.

    `status_code` é `None` quando não houve resposta HTTP nenhuma (conexão recusada,
    timeout). Zero seria mais cômodo e mentiria: `None` obriga quem consome a decidir o
    que fazer com o caso, que é o comportamento certo — sem resposta é INDISPONIVEL.

    `body` é o JSON decodificado quando dá, e o texto cru quando não dá (um proxy no
    caminho devolve HTML, e perder isso cega o diagnóstico).

    `latencia_ms` cobre só a ida e volta HTTP, não a validação nem a serialização.
    """

    status_code: int | None = None
    body: Any = None
    latencia_ms: int


class TractianClient:
    """Cliente síncrono da API do parceiro, um por run.

    `seed` e `user_id` são da run inteira, por isso ficam no construtor: cenário nenhum
    troca de usuário ou de ambiente no meio. `transport` existe para os testes injetarem
    `httpx.MockTransport` — é a única costura de que eles precisam.
    """

    def __init__(
        self,
        base_url: str,
        *,
        user_id: str | None = None,
        seed: str | None = None,
        timeout_s: float = TIMEOUT_PADRAO_S,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url
        self.user_id = user_id
        self.seed = seed

        headers = {"x-user-id": user_id} if user_id else {}
        self._http = httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=timeout_s,
            transport=transport,
        )

    def get(self, endpoint: str, params: dict[str, Any] | None = None) -> RawResponse:
        """Faz um GET e devolve o que voltou, com a latência medida.

        Erro HTTP não levanta exceção: 404 em ativo inexistente e 403 sem permissão são
        fatos do ambiente que os cenários exercitam de propósito, e precisam chegar ao
        trace como resposta, não como stack trace. Só falha de transporte é convertida —
        em `status_code=None` —, e mesmo assim a latência é preservada, porque o tempo
        até o timeout é o dado relevante quando a API não responde.
        """
        return self._requisitar("GET", endpoint, params=params)

    def post(self, endpoint: str, corpo: dict[str, Any] | None = None) -> RawResponse:
        """Faz um POST de ação e devolve o que voltou. Mesmas garantias do `get`.

        As ações de impacto (`reprocess`, `request-specialist`, `request-retraining`,
        `escalate`) não recebem `seed`: o modo probabilístico só vale para consulta
        (`api/app/prob.py`), e mandar a semente numa escrita sugeriria um determinismo que
        a API não oferece nesses endpoints.
        """
        return self._requisitar("POST", endpoint, corpo=corpo)

    def patch(self, endpoint: str, corpo: dict[str, Any] | None = None) -> RawResponse:
        """Faz um PATCH de ação. Só `updateAssetConfig` usa este verbo."""
        return self._requisitar("PATCH", endpoint, corpo=corpo)

    def _requisitar(
        self,
        metodo: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        corpo: dict[str, Any] | None = None,
    ) -> RawResponse:
        """O caminho único de saída HTTP. Ver as garantias documentadas em `get`."""
        query = self._com_seed(params) if metodo == "GET" else params

        inicio = time.perf_counter()
        try:
            resposta = self._http.request(metodo, endpoint, params=query, json=corpo)
        except httpx.TransportError as erro:
            # Só falha de rede. `httpx.InvalidURL` e afins continuam subindo: são bug de
            # quem chamou, e disfarçá-los de indisponibilidade da API esconderia o bug.
            return RawResponse(
                status_code=None,
                body={"code": "TRANSPORT_ERROR", "message": str(erro)},
                latencia_ms=self._ms_desde(inicio),
            )
        latencia_ms = self._ms_desde(inicio)

        return RawResponse(
            status_code=resposta.status_code,
            body=_corpo(resposta),
            latencia_ms=latencia_ms,
        )

    def _com_seed(self, params: dict[str, Any] | None) -> dict[str, Any]:
        """Junta o `seed` da run aos parâmetros da chamada. Quem chama tem a última palavra."""
        query = dict(params or {})
        if self.seed is not None and "seed" not in query:
            query["seed"] = self.seed
        return query

    @staticmethod
    def _ms_desde(inicio: float) -> int:
        return round((time.perf_counter() - inicio) * 1000)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> TractianClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


def _corpo(resposta: httpx.Response) -> Any:
    """JSON quando dá; texto cru quando não dá."""
    try:
        return resposta.json()
    except ValueError:
        return resposta.text
