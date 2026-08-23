"""O verificador de modelo local — que distingue download truncado de arquivo corrompido.

Em 23/08 o 14B parou em 15% do segundo shard e o LM Studio devolveu `Tensor 'lm_head.weight'
invalid data offsets ... Perhaps an incomplete download or corrupt file?`. As duas hipóteses da
mensagem pedem reações opostas: truncado se resolve esperando, corrompido se resolve apagando e
rebaixando. Distinguir custou minutos que a T0b — tempo-caixa de 3h — não tem de sobra.

Os testes montam safetensors sintéticos em `tmp_path`; nenhum modelo real é lido.
"""
from __future__ import annotations

import importlib.util
import json
import struct
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def checador():
    spec = importlib.util.spec_from_file_location(
        "checar_modelo_local", RAIZ / "scripts" / "checar_modelo_local.py"
    )
    modulo = importlib.util.module_from_spec(spec)
    sys.modules["checar_modelo_local"] = modulo
    spec.loader.exec_module(modulo)
    return modulo


def escrever_shard(caminho: Path, tensores: dict[str, tuple[int, int]], *, bytes_de_dados: int):
    """Escreve um safetensors com o header dado e `bytes_de_dados` de corpo.

    Passar menos bytes do que os offsets exigem é como um download interrompido se parece.
    """
    header = {nome: {"dtype": "F16", "shape": [1], "data_offsets": list(par)}
              for nome, par in tensores.items()}
    bruto = json.dumps(header).encode()
    caminho.write_bytes(struct.pack("<Q", len(bruto)) + bruto + b"\0" * bytes_de_dados)


@pytest.fixture
def modelo(tmp_path):
    def montar(tensores, *, bytes_de_dados, com_indice=True):
        shard = tmp_path / "model-00001-of-00001.safetensors"
        escrever_shard(shard, tensores, bytes_de_dados=bytes_de_dados)
        if com_indice:
            (tmp_path / "model.safetensors.index.json").write_text(
                json.dumps({"weight_map": {nome: shard.name for nome in tensores}})
            )
        return tmp_path
    return montar


def test_modelo_inteiro_nao_reclama(checador, modelo):
    pasta = modelo({"a": (0, 64), "b": (64, 128)}, bytes_de_dados=128)
    assert checador.checar_modelo(pasta) == []


def test_shard_truncado_diz_quanto_falta(checador, modelo):
    """A mensagem tem de dizer "faltam N" — é o que separa esperar de rebaixar o download."""
    pasta = modelo({"a": (0, 64), "b": (64, 1024)}, bytes_de_dados=100)
    (problema,) = checador.checar_modelo(pasta)
    assert "truncado" in problema
    assert "faltam" in problema


def test_a_truncagem_e_localizada_no_primeiro_tensor_a_estourar(checador, modelo):
    """Não no de maior offset, que é o último do arquivo.

    O carregador reclama do último (`lm_head`), mas quem diz onde o download parou é o
    primeiro. Apontar o último manda procurar no lugar errado.
    """
    pasta = modelo(
        {"comeca_no_corte": (100, 900), "vai_mais_longe": (900, 1000)}, bytes_de_dados=200
    )
    (problema,) = checador.checar_modelo(pasta)
    assert "`comeca_no_corte`" in problema
    assert "vai_mais_longe" not in problema


def test_cabecalho_maior_que_o_arquivo_e_download_truncado_e_nao_json_invalido(checador, tmp_path):
    """Corte antes do fim do header: a mensagem não pode culpar o JSON."""
    shard = tmp_path / "model.safetensors"
    shard.write_bytes(struct.pack("<Q", 5000) + b'{"a":')
    (problema,) = checador.checar_modelo(tmp_path)
    assert "truncado" in problema


def test_shard_citado_no_indice_mas_ausente_no_disco(checador, tmp_path):
    (tmp_path / "model.safetensors.index.json").write_text(
        json.dumps({"weight_map": {"a": "model-00002-of-00002.safetensors"}})
    )
    problemas = checador.checar_modelo(tmp_path)
    assert any("não existe" in p for p in problemas)


def test_pasta_sem_indice_e_sem_modelo_solto(checador, tmp_path):
    (problema,) = checador.checar_modelo(tmp_path)
    assert "model.safetensors" in problema


def test_modelo_de_arquivo_unico_dispensa_indice(checador, tmp_path):
    escrever_shard(tmp_path / "model.safetensors", {"a": (0, 32)}, bytes_de_dados=32)
    assert checador.checar_modelo(tmp_path) == []
