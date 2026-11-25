# CS2 Coach

Lê as demos das suas partidas e o que o jogo informa ao vivo, e mostra no que melhorar: dano por arma, ranking das suas
melhores armas, dicas de treino, suas configurações explicadas e a comparação antes/depois de cada mudança.
Tudo roda **só no seu computador**. Não toca na memória do CS2 (usa apenas arquivos de demo e a integração oficial do jogo),
então **não há risco de VAC**.

## Para usar (você ou seus amigos, Windows)

**Opção A: instalador (recomendado, não precisa de Python)**
1. Rode `CS2Coach-Setup.exe` e clique em Avançar.
2. Abra o **CS2 Coach** pelo menu Iniciar. O navegador abre sozinho no assistente de primeira vez (1 minuto).
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
- **Analisar demos**: análise manual, se quiser. Por padrão as demos novas são analisadas sozinhas.
- **Ao vivo**: painel durante o jogo (detecta o modo sozinho) e as sessões salvas. Sessões de treino têm página detalhada
  mas não entram nos números; só Competitivo/Premier e Braço Direito contam (mude em Ajustes).
- **Ajustes**: conta, DPI, Hz, pastas, modos que contam.

O programa fica com um ícone perto do relógio do Windows (Abrir, Pasta dos dados, Sair). Se ele já estiver aberto,
abrir de novo só leva você ao navegador.

## Seus dados
Ficam em `%APPDATA%\CS2Coach` (cole isso na barra do Explorador). Atualizar ou reinstalar **não apaga** o histórico.
Backup: copie essa pasta. Começar do zero: apague-a com o programa fechado. Nada sai do seu computador.
Se você usava a versão antiga (pasta `dados` ao lado do programa), ela é copiada sozinha na primeira abertura,
desde que você extraia a versão nova por cima da pasta antiga.

Imagens reais das armas: coloque PNGs em `%APPDATA%\CS2Coach\armas_img\` com o nome interno (`ak47.png`, `awp.png`...).

## Publicar uma versão (GitHub Releases + aviso automático)

**Uma vez só**
1. Crie uma conta em github.com e um repositório **público** (ex.: `cs2coach`). Público é necessário para o programa dos seus
   amigos conseguir perguntar se há versão nova; o código também fica visível. Releases de repositório privado não funcionam com o aviso.
2. Envie esta pasta para o repositório (GitHub Desktop: *Add local repository > Publish*; ou `git init`, `git add .`, `git commit`, `git push`).
   O arquivo `.gitignore` já deixa de fora `build`, `dist`, `dados` e arquivos grandes.
3. No GitHub: *Settings > Actions > General > Workflow permissions* e marque **Read and write permissions**.

**A cada versão nova**
1. Substitua os arquivos pelos da versão nova (o que eu enviar) e faça *commit* e *push*.
2. Crie a tag da versão e envie: `git tag v5.0.1` e `git push origin v5.0.1` (ou, no GitHub: *Releases > Draft a new release*, crie a tag e publique).
3. Espere uns 10 minutos (aba *Actions*). O GitHub gera o programa, testa se abre e publica em *Releases* o `CS2Coach-Setup.exe`
   e o `CS2Coach-portatil.zip`. O número da versão vem da tag (v5.0.1 vira 5.0.1) e o nome do repositório é preenchido sozinho.
4. Pronto: quem já usa o CS2 Coach vê um aviso azul "Nova versão disponível" com o botão de baixar (consulta a cada poucas horas,
   só lê o número da versão; nada é enviado). Para teste, a aba *Actions > Run workflow* gera os arquivos sem publicar.

**No seu Windows (alternativa sem GitHub):** instale o Python e o Inno Setup 6, depois `pip install -r requirements-build.txt`,
`python gerar_icone.py`, `pyinstaller cs2coach.spec --noconfirm` e compile `installer\cs2coach.iss` no Inno Setup.

## Linha de comando (avançado)
`python app\hub.py --help` · `python app\analisar_demo.py arquivo.dem --jogador SeuNick` · `python app\ao_vivo.py --diagnostico`
