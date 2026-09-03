"""
`tapieval.vivo` — a consulta ao vivo: uma pergunta nova, executada de verdade.

POR QUE ESTE PACOTE É SEPARADO DE `tapieval.app`
    `app/` gera UM html autocontido que abre por duplo clique e **não pode** rodar o agente:
    `test_a_aplicacao_nao_importa_nada_que_execute_agente_ou_rede` prende esse desenho, e ele
    continua valendo. Uma página que dependesse de GPU e de endpoint no ar falharia na hora em
    que precisa funcionar, e a apresentação não pode ter essa dependência.

    Mas "a página gravada não executa" nunca foi "o projeto não executa". O runner sempre
    executou; o que faltava era uma porta pela qual uma pergunta **que não está no corpus**
    chegasse até ele. É este pacote. Ele importa o runner, a camada MCP e a rede — tudo que
    `app/` tem proibido — e por isso mora fora de `app/`.

    Os dois entregáveis, e quando usar cada um:

        make app        `app/copiloto.html`   navega execuções gravadas. Offline. Plano B do palco.
        make copiloto   servidor em :7000     pergunta nova, agente rodando, trace ao vivo.

    A MESMA página serve os dois: servida por http ela habilita a consulta; aberta por
    `file://` ela continua sendo o inspetor gravado. Uma página, dois modos — e o modo é
    consequência de onde ela está, não de uma cópia divergente.

O QUE UMA PERGUNTA NOVA NÃO TEM
    Gabarito. Ninguém escreveu, antes de a pergunta existir, quais evidências eram obrigatórias
    nem qual decisão era a certa. O instrumento não fica mudo — fica parcial, e a parte que
    resta está delimitada em `scoring/sem_gabarito.py`: 4 dos 19 códigos, com os outros 15
    nomeados na tela junto com o motivo de não terem sido medidos.

    Isso não é uma desculpa embutida na demonstração. É a demonstração: a fronteira entre o que
    custa gabarito, o que custa LLM e o que sai de graça do trace é a pergunta que o trabalho
    inteiro responde, e aqui ela aparece ao vivo, num caso que ninguém preparou.
"""
