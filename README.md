# CS2 Coach

Lê as demos das suas partidas e o que o jogo informa ao vivo, e mostra no que melhorar: dano por arma, ranking das suas
melhores armas, dicas de treino, suas configurações explicadas e a comparação antes/depois de cada mudança.
Tudo roda **só no seu computador**. Não toca na memória do CS2 (usa apenas arquivos de demo e a integração oficial do jogo),
então **não há risco de VAC**.

## Para usar (você ou seus amigos, Windows)

**Opção A: instalador (recomendado, não precisa de Python)**
0. Baixe o instalador em https://github.com/Pexoss/cs2coach/releases/latest/download/CS2Coach-Setup.exe
1. Rode `CS2Coach-Setup.exe` e clique em Avançar.
2. Abra o **CS2 Coach** pelo menu Iniciar. O programa abre na **própria janela** (sem navegador) no assistente de primeira vez (1 minuto).
3. Pronto. Baixe as demos no CS2 (Seu perfil > Partidas > Baixar) e o programa analisa sozinho.

O Windows pode avisar "O Windows protegeu seu PC" porque o instalador não tem assinatura digital paga.
Clique em **Mais informações > Executar assim mesmo**. Alguns antivírus também podem reclamar: é falso positivo comum
em programas feitos com PyInstaller.

**Opção B: com Python instalado**
Dê dois cliques em `Iniciar CS2 Coach.bat`. Na primeira vez ele prepara tudo sozinho (precisa de internet).

## O que tem no Hub
- **Visão geral**: evolução de ADR, K/D e HS%, melhores armas e as dicas que mais se repetem.
- **Partidas**: tudo que já foi analisado, com a **configuração usada em cada partida**.
- **Armas**: dano por arma e ranking (nota, dano, kills, precisão, dano por tiro, HS%).
- **Configurações**: seus arquivos do CS2 explicados em português, sugestões (inclusive baseadas nas suas partidas) e
  antes/depois de cada mudança (ADR, K/D, HS, rifles e AWP).
- **Evolução**: seu jogo a cada 15 partidas e por mapa (K/D, ADR, HS, tempo para matar, rifle parado/correndo, mortes sem atirar).
- **Treino**: plano semanal montado dos seus pontos fracos, com treino do dia, marcação do que foi feito e o efeito nas partidas seguintes.
- Em cada partida: **métricas estilo Scope.gg** (HS por acerto, tempo para matar, kills paradas...) com nota contra o seu histórico, e
  **cada kill e morte** (onde, arma, distância, headshot, parede, fumaça...) num mapa de pontos e em tabela.
- **Analisar demos**: análise manual, se quiser. Por padrão as demos novas são analisadas sozinhas.
- **Ao vivo**: painel durante o jogo (detecta o modo sozinho) e as sessões salvas. Sessões de treino têm página detalhada
  mas não entram nos números; só Competitivo/Premier e Braço Direito contam (mude em Ajustes).
- **Ajustes**: conta, DPI, Hz, pastas, modos que contam.

O programa fica com um ícone perto do relógio do Windows (Abrir, Pasta dos dados, Sair). Se ele já estiver aberto,
abrir de novo só traz a janela para a frente. Fechar a janela (X) mantém o programa rodando perto do relógio; use "Sair" no ícone para encerrar.
A janela usa o WebView2 do Windows (já vem no Windows 11); se faltar, o programa abre no modo aplicativo do Edge/Chrome.

## Seus dados
Ficam em `%APPDATA%\CS2Coach` (cole isso na barra do Explorador). Atualizar ou reinstalar **não apaga** o histórico.
Backup: copie essa pasta. Começar do zero: apague-a com o programa fechado. Nada sai do seu computador.
Se você usava a versão antiga (pasta `dados` ao lado do programa), ela é copiada sozinha na primeira abertura,
desde que você extraia a versão nova por cima da pasta antiga.

Imagens reais das armas: coloque PNGs em `%APPDATA%\CS2Coach\armas_img\` com o nome interno (`ak47.png`, `awp.png`...).

## Publicar uma versão (100% automático)
O GitHub faz tudo sozinho: **a cada commit que muda o programa** (pastas `app/` e `installer/`, `cs2coach.spec`, `icone.ico`,
`requirements*.txt`) na branch `main`, ele gera o instalador, testa se abre e publica uma nova versão em *Releases*, com a
numeração `5.0.N` (N sobe sozinho a cada commit). Ninguém precisa criar tag nem gerar o instalador no próprio PC.

- **Link fixo para baixar o instalador (para mandar aos amigos):**
  `https://github.com/Pexoss/cs2coach/releases/latest/download/CS2Coach-Setup.exe`
- Cada Release traz **só** o `CS2Coach-Setup.exe`, que é o que a pessoa precisa baixar.
- Quem já usa o programa vê um aviso azul "Nova versão disponível" com o botão de baixar (consulta a cada poucas horas,
  só lê o número da versão; nada é enviado).
- Para trocar a versão "grande" (5.0 para 5.1), edite `VERSAO_BASE` no arquivo `.github/workflows/build.yml`.
- Para testar sem publicar: aba *Actions > Gerar e publicar o CS2 Coach > Run workflow*; o instalador fica em *Artifacts*.
- Mudanças só no LEIA-ME não geram versão nova.

## Linha de comando (avançado)
`python app\hub.py --help` · `python app\analisar_demo.py arquivo.dem --jogador SeuNick` · `python app\ao_vivo.py --diagnostico`


## Novidades (ao vivo, configuração e janela)

- **Avisos ao vivo mais confiáveis**: "morreu sem atirar" só aparece se não houve tiro, dano nem kill nos últimos 4 s (e é confirmado no fim do round, quando kills e dano já chegaram). Kills que o jogo informa atrasadas entram no round certo.
- **Dano e kills por round ao vivo**: dano causado, arma e momento de cada kill, headshot e posição. Distância do alvo e local do acerto o jogo **não informa ao vivo**; esses dados vêm das demos (aba "Kills e mortes" de cada partida: dano por round, onde acertou e distância).
- **Configuração sempre atual**: o CS2 Coach relê a sua configuração (sens, mira, vídeo) ao começar cada partida ao vivo, antes de analisar demos e a cada 20 s. O valor mais recentemente gravado vence (autoexec ou opções do jogo).
- Janela com cantos arredondados, barra de título na cor do app e ícone próprio na barra de tarefas. Se o ícone antigo continuar, desafixe e fixe de novo o atalho.
- O arquivo de integração agora também pede a posição do jogador; o CS2 Coach atualiza o arquivo sozinho e pede para reiniciar o CS2 uma vez.
