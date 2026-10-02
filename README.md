# PC Monitor

Monitor do PC em tempo real para Windows 11, com **widgets flutuantes** e um **app de configurações**.
Também grava um **histórico em arquivo** para investigar quedas e telas azuis.

## Instalação (uma vez)

1. **Baixe o projeto:** na página do projeto no GitHub (https://github.com/SEU-USUARIO/pcmonitor), clique no
   botão verde **`Code`** e escolha **`Download ZIP`**. Se o Windows reclamar do arquivo baixado, clique nele
   com o botão direito, abra **Propriedades**, marque **Desbloquear** e confirme.
2. **Extraia o zip:** botão direito no arquivo, **"Extrair tudo…"**. Não execute nada de dentro do zip — o
   Windows mostra o conteúdo como se fosse uma pasta comum, mas os `.bat` não funcionam ali. Deixe a pasta
   extraída em um lugar definitivo (por exemplo `C:\pcmonitor`): mover depois quebra a opção "Iniciar com o
   Windows", que grava o caminho completo.
3. Instale o Python 3.10 a 3.13 em python.org, marcando **"Add Python to PATH"**.
4. Dê dois cliques em `instalar.bat`. Ele cria o ambiente, instala as dependências e baixa o
   LibreHardwareMonitor (leitura de temperatura da CPU, placa-mãe e SSDs) para a pasta `libs/`.
5. Dê dois cliques em `executar.bat`. Ele pede permissão de administrador, necessária para ler as
   temperaturas. O ícone aparece na bandeja, perto do relógio.

Quem já usa o Git pode substituir os passos 1 e 2 por `git clone https://github.com/SEU-USUARIO/pcmonitor.git`,
o que facilita atualizar depois com `git pull`.

## Como usar

- **Widgets:** arraste com o mouse (grudam nas bordas da tela). Clique direito: travar posição, ocultar,
  abrir configurações. Duplo clique abre as configurações daquele widget.
- **Aba Widgets:** crie quantos widgets quiser, cada um com suas informações, ordem, tamanho, opacidade, cor,
  layout vertical ou horizontal, barras e título. Botões ↖ ↗ ↙ ↘ levam o widget ao canto de qualquer tela.
- **Aba Ao vivo:** todas as leituras disponíveis agora, com busca.
- **Aba Geral:** intervalo de atualização, limites de cor (verde/amarelo/vermelho), alertas na bandeja,
  histórico e iniciar com o Windows.
- **Aba Diagnóstico:** serve para qualquer problema do PC. Escolha **o que está acontecendo** (lentidão, tela azul
  ou reinícios, programas fechando, calor, disco, internet, vídeo/jogos, Windows Update) ou use o **Check-up geral**.
  Ele lê eventos do Windows, BIOS, memória, discos, drivers, rede, segurança, inicialização e o histórico do monitor,
  mostra a **causa mais provável** com evidências e o que fazer, em ordem, e separa os achados do seu foco dos demais.
  Ao fim de cada diagnóstico, o relatório (.txt) e os dados brutos (.json) são salvos sozinhos em duas pastas, `Documentos\PCMonitor\Relatorios` e `Documentos\PCMonitor\Dados brutos` (dá para mudar a pasta e abrir ambas pela própria aba). Em **Marco de comparação** você registra uma mudança
  (ex.: "EXPO desativado") e o relatório compara as quedas antes e depois. A subaba **Comandos sugeridos** lista comandos de PowerShell ligados ao seu problema (ordenados pelas causas prováveis). **Nenhum é executado automaticamente**: você lê a explicação, pode editar e copiar o comando para o seu PowerShell, ou clicar em *Executar aqui…* e confirmar. Os que alteram o sistema vêm marcados com ⚠. A subaba **Ferramentas** roda SFC, DISM e
  CHKDSK (somente leitura) com a saída ao vivo e abre atalhos do Windows (Monitor de Confiabilidade, Visualizador de
  Eventos, Teste de Memória, etc.).
- **Aba Histórico:** sessões gravadas, com máximos e últimos valores registrados antes de cada queda.

## Investigando as quedas

A cada 5 segundos (ajustável) o programa grava temperaturas, uso de CPU/GPU, RAM e o processo que mais usa
memória, em `%APPDATA%\PCMonitor\historico\sessao-*.csv`. Cada linha é forçada para o disco na hora.

Quando você fecha o programa normalmente, ele marca a sessão como encerrada. Se o PC cair ou reiniciar, a
sessão fica sem essa marca. Na próxima abertura você recebe um aviso com os últimos valores registrados
(temperatura, RAM etc.), e a aba **Histórico** mostra o resumo.

Interpretação rápida: temperatura de CPU/GPU subindo bem antes da queda indica calor; RAM e pagefile
perto do limite indicam falta de memória. Se tudo estiver normal até o último registro, o problema tende a
ser hardware instável (por exemplo a RAM com EXPO/DOCP) ou driver, não calor nem falta de memória.

## Sobre o diagnóstico

Ele não altera nada no sistema (exceto os botões de reparo da aba Ferramentas, que pedem confirmação).
O resultado é uma estimativa baseada em padrões conhecidos, não uma certeza: confirme com os testes indicados.
Se algum dado não aparecer, use **Salvar dados brutos** e envie o arquivo para análise.

Nos widgets há também a métrica **Sem queda há**, que mostra o tempo desde o último reinício inesperado.

## Se a temperatura da CPU não aparecer

1. Rode `testar-sensores.bat` e veja `sensores.txt`. Ele mostra o que foi lido e a mensagem de erro, se houver.
2. Confirme que abriu como administrador (o `executar.bat` já faz isso).
3. Versões recentes do LibreHardwareMonitor podem precisar do driver **PawnIO** para ler sensores do Ryzen.
   Se o erro mencionar driver, instale o PawnIO (https://pawnio.eu) e reinicie o programa.
4. Se a `libs/` estiver vazia, rode `python setup_lhm.py` ou baixe o zip manualmente (o script explica como).

GPU NVIDIA (uso, temperatura, VRAM, watts) é lida direto do driver e não precisa de administrador.

## Observações

- Configurações ficam em `%APPDATA%\PCMonitor\config.json`.
- "Iniciar com o Windows" usa o Agendador de Tarefas com privilégios elevados quando o programa está
  em administrador; sem administrador, as temperaturas da CPU podem não aparecer.
- Para apagar tudo, feche o programa e remova a pasta do projeto e `%APPDATA%\PCMonitor`.
