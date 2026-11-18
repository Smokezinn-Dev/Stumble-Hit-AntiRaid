# 🛡️ Stumble Hit AntiRaid v13.5

Bot Discord completo focado em **moderação, proteção, anti-raid, anti-nuke, anti-escalation, logs, embed builder, mover canais/categorias, painel anti-raid interativo, captcha multi-tipo, score de risco no join, anti IP logger, anti evadir mute, gráficos de estatísticas e performance extrema com paralelismo**.

Sem economia. Prefixo padrão: `>`. Multi-guild (até 2 servidores). Slash commands habilitados.

Compatível com Railway, Discloud e VPS.

---

## 📋 ÍNDICE

- [Visão Geral](#-visão-geral)
- [Requisitos](#-requisitos)
- [Estrutura de Diretórios](#-estrutura-de-diretórios)
- [Instalação](#-instalação)
- [Variáveis de Ambiente](#-variáveis-de-ambiente)
- [Execução](#-execução)
- [Deploy](#-deploy)
- [Arquitetura](#-arquitetura)
- [Módulos Controláveis](#-módulos-controláveis)
- [Comandos por Categoria](#-comandos-por-categoria)
- [🆕 Novidades da v13.5](#-novidades-da-v135)
- [Painel Anti-Raid Interativo](#-painel-anti-raid-interativo)
- [Coleções MongoDB](#-coleções-mongodb)
- [Segurança Aplicada](#-segurança-aplicada)
- [Otimizações](#-otimizações)
- [Métricas de Performance](#-métricas-de-performance)
- [Erros Comuns e Soluções](#-erros-comuns-e-soluções)
- [Lições Aprendidas](#-lições-aprendidas)
- [Licença](#-licença)
- [Autor](#-autor)

---

## 🎯 VISÃO GERAL

**Stumble Hit AntiRaid v13.5** é a versão mais completa da suíte de moderação e proteção.

**Destaques:**
- 🎛️ **Controle total** por servidor (módulos, comandos, canal, manutenção)
- 🎨 **Branding** (nick, cor, footer customizados por servidor)
- 💾 **Backup inteligente** (canais, cargos, membros, configs, textos)
- 🛡️ **Moderação completa** (punir, canais, cargos, voz, conteúdo)
- 🤖 **Automod** (7 filtros automáticos configuráveis)
- 🚨 **Anti-Nuke + Anti-Raid** (monitor de audit log + quarentena)
- 🔒 **Anti-Escalation** (proteção contra escalada de privilégios)
- 🎨 **Embed Builder** avançado (com sendmany/clonemany)
- 🗂️ **Mover canais/categorias** por comando
- 🆕 **Painel Anti-Raid interativo** (persistente, tempo real, 3 páginas)
- 🆕 **Anti IP Logger** (bloqueia grabify, iplogger e +50 domínios)
- 🆕 **Anti Evadir Mute** (persiste timeout quando user sai/volta)
- 🆕 **Score de Risco no Join** (0-100, 3 faixas de ação configuráveis)
- 🆕 **Captcha multi-tipo** (math, image, emojis, question, button)
- 🆕 **Gráficos de estatísticas** (PNG com Pillow)
- ⚡ **Paralelismo extremo** (run_parallel com retry automático)
- 🩺 **Sistema** (health check, memory monitor, auto-GC)

**Performance:**
- RAM: ~75 MB
- Loop lag: Excelente (< 100 ms)
- Startup: ~3-4s
- 31 cogs carregados (15 obrigatórios + 16 opcionais)
- ~260 comandos totais
- ~68 coleções MongoDB + ~115 índices

---

## ⚙️ REQUISITOS

- Python 3.10 ou superior
- MongoDB Atlas (ou local)
- discord.py 2.3+
- pymongo 4.5+
- dnspython 2.4+
- psutil 5.9+ (opcional, para health check detalhado)
- Pillow 10.0+ (para gráficos e captcha de imagem)

---

## 📁 ESTRUTURA DE DIRETÓRIOS

```

Shadow-Bot/
│
├── main.py                              ← v13.5 (bot principal + cog ModAll)
├── config.py                            ← Validação estrita + multi-guild
├── database.py                          ← v12.3 (pool tuning + índices + TTL)
├── embeds.py                            ← Brand por guild
├── embeds_professional.py               ← Logs profissionais
├── utils.py                             ← v8.0 (cache + guild gate + run_parallel)
├── optimizer.py                         ← Motor pesado de otimização
├── requirements.txt                     ← Dependências
├── discloud.config                      ← Config para Discloud
├── README.md                            ← Este arquivo
│
├── CONTROLE E CONFIG
├── commands_control.py                  ← Controle total
├── commands_branding.py                 ← Personalização por guild
├── commands_backup.py                   ← v8.0 (restore paralelo)
│
├── STAFF
├── commands_staff.py                    ← Staff + auto-roles
├── commands_staff_config.py             ← Config staff
│
├── PROTEÇÃO
├── commands_automod.py                  ← Filtros automáticos
├── commands_antinuke.py                 ← v13.4 (anti-nuke + anti-raid + whitelist managers)
├── commands_antinuke_panel.py           ← v1.5 (painel interativo 3 páginas) 🆕
├── commands_antiescalation.py           ← Anti-escalation (12 regras)
├── commands_protection_toggle.py        ← Toggle proteção users
├── commands_whitelist_confirm.py        ← Confirmação DM
├── commands_bot_whitelist_protection.py ← v1.2 (proteção especial bots)
├── commands_antilink_protection.py      ← 🆕 v1.0 (Anti IP Logger)
├── commands_anti_evade.py               ← 🆕 v1.0 (Anti Evadir Mute)
├── commands_join_risk.py                ← 🆕 v1.0 (Score de Risco no Join)
│
├── REGISTRO
├── commands_cases.py                    ← Cases + notes + appeals
├── commands_moderation_extra.py         ← v9.0 (purgemulti paralelo)
├── commands_moderation_extra2.py        ← v2.0 (hideall/roleall paralelos)
│
├── LOGS
├── commands_logs.py                     ← Logs customizáveis
├── commands_event_logs.py               ← v1.1 (logs de eventos)
├── commands_stats_graph.py              ← 🆕 v1.0 (gráficos com Pillow)
│
├── AVISOS E PERMISSÕES
├── commands_warn_system.py              ← v1.3 (warn + warnbatch)
├── commands_perm_categories.py          ← Permissões por categoria
├── commands_descriptions.py             ← Help rico
├── commands_raid_suspects.py            ← Detecção de raid
│
├── EXTRAS
├── commands_verification.py             ← v9.0 (5 tipos de captcha) 🆕
├── commands_slash.py                    ← Slash commands + context menus
├── commands_optimizer.py                ← Comandos do otimizador
│
├── v12.5
├── commands_embed_builder.py            ← v1.5 (sendmany/clonemany)
└── commands_channel_mover.py            ← v1.2 (fallback paralelo)

```

**Total:** 41 arquivos (36 .py + discloud.config + requirements.txt + README.md + main.py)

**Distribuição:**
- 1 motor independente (optimizer.py, não é cog)
- 31 cogs carregados (15 obrigatórios + 16 opcionais)
- 5 módulos utilitários
- 2 arquivos de config

---

## 📦 INSTALAÇÃO

**1. Criar a pasta:**

```bash
mkdir Shadow-Bot
cd Shadow-Bot
```

2. Criar ambiente virtual:

```bash
python -m venv venv

# Linux/Mac
source venv/bin/activate

# Windows
venv\Scripts\activate
```

3. Instalar dependências:

```bash
pip install -r requirements.txt
```

---

🔐 VARIÁVEIS DE AMBIENTE

Crie um arquivo .env na raiz (ou configure no painel do Railway/Discloud):

```
# OBRIGATÓRIAS
DISCORD_TOKEN=seu_token_do_bot_moderacao
MONGODB_URL=mongodb+srv://usuario:senha@cluster.mongodb.net/?retryWrites=true&w=majority

# OPCIONAIS
DB_NAME=gt_bot_moderacao
PREFIX=>
GUILD_IDS=123456789012345678,987654321098765432
ENABLE_DEBUG=false

# Backup automático
BACKUP_ENABLED=true
BACKUP_AUTO_HOUR=4
BACKUP_MAX_PER_GUILD=10
BACKUP_LOG_WEBHOOK=

# Anti-Nuke / Anti-Raid
ANTINUKE_ENABLED=true
ANTIRAID_ENABLED=true
ANTINUKE_NOTIFY_OWNER=true
ANTINUKE_ROLLBACK=true

# Bot Approval

# Performance
MEMORY_GC_THRESHOLD_MB=380
MEMORY_MONITOR_INTERVAL_MIN=3
RATE_LIMIT_RATE=3
RATE_LIMIT_PER=5
```

Tabela completa:

Variável Obrigatória Padrão Descrição
DISCORD_TOKEN Sim — Token do bot (Developer Portal)
MONGODB_URL Sim — String de conexão Mongo
DB_NAME Não gt_bot_moderacao Nome do banco
PREFIX Não  Prefixo padrão
GUILD_IDS Não — IDs separados por vírgula
ENABLE_DEBUG Não false Logs verbosos
BACKUP_ENABLED Não true Backup automático
BACKUP_AUTO_HOUR Não 4 Hora UTC do backup
BACKUP_MAX_PER_GUILD Não 10 Backups guardados
BACKUP_LOG_WEBHOOK Não — Webhook do backup
ANTINUKE_ENABLED Não true Anti-nuke global
ANTIRAID_ENABLED Não true Anti-raid global
ANTINUKE_NOTIFY_OWNER Não true DM pro dono
ANTINUKE_ROLLBACK Não true Rollback automático
BOT_APPROVAL_TIMEOUT Não 300 Tempo padrão de confirmação de bots whitelisted (segundos)
MEMORY_GC_THRESHOLD_MB Não 380 Auto-GC
MEMORY_MONITOR_INTERVAL_MIN Não 3 Intervalo do monitor
RATE_LIMIT_RATE Não 3 Rate limit (ações)
RATE_LIMIT_PER Não 5 Rate limit (segundos)

---

▶️ EXECUÇÃO

Modo desenvolvimento:

```bash
python main.py
```

Modo produção (Linux):

```bash
nohup python main.py > bot.log 2>&1 &
```

---

🚀 DEPLOY

Railway:

1. Crie um novo projeto em railway.app
2. Conecte ao repositório Git
3. Adicione as variáveis de ambiente na aba Variables
4. Start Command: python main.py
5. Railway detecta requirements.txt automaticamente

Discloud:

O arquivo discloud.config já está pronto:

```
NAME=Stumble Hit AntiRaid
MAIN=main.py
RAM=100
status=online
activity_name=🛡️ Moderação | >help
activity_type=watching
ignore=.git,__pycache__,*.pyc,*.log,gt_bot_moderacao.db
```

Passos:

1. Baixe o app Discloud
2. Faça upload da pasta inteira (com discloud.config)
3. Configure as variáveis de ambiente no painel
4. O bot sobe automaticamente

---

🏗️ ARQUITETURA

Fluxo de verificação por mensagem:

```
Mensagem recebida
   ↓
É comando? (prefixo > ou custom)
   ↓ sim
Guild está em manutenção?
   ↓ não
Usuário é bypass? → sim → executa
   ↓ não
Comando pertence a módulo?
   ↓ sim
Módulo está ativo?
   ↓ sim
Comando desativado no servidor?
   ↓ não
Comando bloqueado neste canal?
   ↓ não
✅ EXECUTA
```

Isso roda ANTES de processar o comando, economizando CPU quando módulo está off.

Carregamento de cogs em paralelo:

O main.py carrega os cogs em paralelo (com asyncio.gather). Separa em obrigatórios e opcionais:

· Obrigatórios que falham → avisa no log mas continua
· Opcionais que falham → ignora silenciosamente

---

🎛️ MÓDULOS CONTROLÁVEIS

Ativar/desativar via >controlmodules ou >controltoggle <mod> on/off:

Módulo Descrição
staff Sistema de staff, metas e pontos
automod Filtros automáticos
antinuke Proteção contra nuke/raid
cases Cases, notes e appeals
backup Backup/restore
branding Personalização do bot

---

🆕 NOVIDADES DA v13.5

A v13.5 adiciona 5 features focadas em proteção proativa e visualização de dados. Todas são aditivas (não removem nem alteram nada existente).

🚫 Anti IP Logger (commands_antilink_protection.py)

O que faz: Bloqueia links de ~50 serviços de IP Logger conhecidos (Grabify, IPLogger, etc.) que são usados por atacantes para capturar o IP de membros.

Comandos:

```
>antiiplogger                       Painel
>antiiplogger on/off                Ativar/desativar
>antiiplogger list                  Lista domínios bloqueados
>antiiplogger add <domínio>         Adiciona domínio
>antiiplogger remove <domínio>      Remove domínio
>antiiplogger punish warn/timeout/kick/ban
>antiiplogger log #canal            Canal de log
>antiiplogger whitelist @user       User/cargo que ignora
```

🔄 Anti Evadir Mute (commands_anti_evade.py)

O que faz: Quando um usuário em timeout sai do servidor, o Discord remove o timeout automaticamente. Este sistema:

1. Persiste os timeouts ativos no MongoDB
2. Quando o usuário volta, reaplica o timeout com o tempo restante
3. Configurável: pode reaplicar (padrão), kickar ou banir quem tentou evadir

Comandos:

```
>antievade                          Painel
>antievade on/off                   Ativar/desativar
>antievade action timeout/kick/ban  Ação ao detectar evasão
>antievade multiplier 1.5            Multiplica o tempo restante
>antievade log #canal               Canal de log
>antievade list                     Lista timeouts ativos
```

📊 Score de Risco no Join (commands_join_risk.py)

O que faz: Aditivo ao sistema de raid existente. Ele NÃO substitui o commands_antinuke.py. Roda em paralelo, calculando um score de risco (0-100) para cada novo membro baseado em:

· Idade da conta
· Tem avatar?
· Nome suspeito?
· Está em lista de suspeitos?
· Frequência de joins no servidor
· Padrões de raid recentes

3 faixas de ação configuráveis:

· 70-84 → Só avisa no log (padrão)
· 85-94 → Kick (padrão)
· 95+   → Ban (padrão)

Comandos:

```
>joinrisk                           Painel
>joinrisk on/off                    Ativar/desativar
>joinrisk threshold 70              Ajusta limite "avisar"
>joinrisk action 85 kick            Configura ação na faixa
>joinrisk action 95 ban             Configura ação na faixa
>joinrisk log #canal                Canal de log
>joinrisk stats                     Últimos scores
```

✅ Captcha Multi-Tipo (commands_verification.py v9.0)

O que faz: Expande o sistema de verificação atual com 5 tipos de captcha:

1. button — botão simples (como já era)
2. math — conta matemática (como já era)
3. image — letras embaralhadas em PNG (NOVO)
4. emojis — sequência de emojis pra clicar em ordem (NOVO)
5. question — pergunta contextual configurável (NOVO)

Adiciona também:

· Rate limit: 3 tentativas antes de kick
· Timeout: captcha expira em X segundos
· Retrocompatível: configs antigas continuam funcionando

Comandos:

```
>verification method button/math/image/emojis/question
>verification attempts 3            Tentativas antes de kick
>verification timeout 120           Segundos pra resolver
>verification question "..."        Pergunta customizada
>verification setup                 Reenvia mensagem
```

📈 Gráficos de Estatísticas (commands_stats_graph.py)

O que faz: Gera imagens PNG com Pillow mostrando:

· Linha de tempo: punições por dia (últimos 30 dias)
· Top infratores: 5 membros com mais punições
· Pizza: distribuição por tipo (kick/ban/warn/timeout)

Comandos:

```
>statsgraph                         Gráfico padrão (30 dias)
>statsgraph 7                       Últimos 7 dias
>statsgraph 90                      Últimos 90 dias
>statsgraph auto on/off             Envio automático semanal
>statsgraph channel #canal          Canal de envio automático
```

---

📋 COMANDOS POR CATEGORIA

🎛️ CONTROLE TOTAL

```
>control                              Painel principal
>controlmodules                       Painel de módulos (select interativo)
>controltoggle <mod> on/off           Ativar/desativar módulo
>controlcommands                      Lista comandos desativados
>controlcmd <cmd> off/on              Desativa/reativa comando
>controlcmd <cmd> channel #canal      Desativa em canal específico
>controlprefix <novo>                 Muda prefixo do servidor
>controlmaintenance on/off            Modo manutenção
>controlbypass @cargo add/remove      Cargo que ignora tudo
>controlstate                         Estado atual do controle
```

🎨 BRANDING

```
>branding                             Painel
>brandingnickname <nome>              Apelido do bot neste servidor
>brandingcolor <hex>                  Cor dos embeds
>brandingfooter <texto>               Rodapé customizado
>brandingreset                        Resetar tudo
```

💾 BACKUP

```
>backup                               Painel
>backupcreate [nome]                  Criar backup
>backuplist                           Listar backups
>backupinfo <id>                      Detalhes
>backupdelete <id>                    Apagar
>backuprestore <id> [modo]            Restore (all/canais/cargos/membros/config/textos)
>backuptext #canal [remove]           Marcar/desmarcar canal pra salvar texto
>backuptextlimit <n>                  Limite de msgs por canal (10-500)
>backuptextlist                       Lista canais que salvam texto
```

🤖 AUTOMOD

```
>automod                              Painel
>automod on/off                       Ativar/desativar tudo
>automod antispam on/off              Anti-flood
>automod antilink on/off              Bloqueia links
>automod antiinvite on/off            Bloqueia convites Discord
>automod anticaps on/off              Bloqueia CAPS LOCK
>automod antimention on/off           Limite de menções
>automod antizalgo on/off             Bloqueia zalgo
>automod antiduplicate on/off         Bloqueia msg repetida
>automod punishment <tipo>            warn/timeout/kick/ban
>automod log #canal                   Canal de logs
>automod whitelist #canal             Canal que ignora
>automod whitelist @cargo             Cargo que ignora
```

🚫 ANTI IP LOGGER 🆕

```
>antiiplogger                         Painel
>antiiplogger on/off                  Ativar/desativar
>antiiplogger list                    Lista domínios bloqueados
>antiiplogger add <domínio>           Adiciona domínio
>antiiplogger remove <domínio>        Remove domínio
>antiiplogger punish warn/timeout/kick/ban
>antiiplogger log #canal              Canal de log
>antiiplogger whitelist @user         User/cargo que ignora
```

🔄 ANTI EVADIR MUTE 🆕

```
>antievade                            Painel
>antievade on/off                     Ativar/desativar
>antievade action timeout/kick/ban    Ação ao detectar evasão
>antievade multiplier 1.5             Multiplica tempo restante
>antievade log #canal                 Canal de log
>antievade list                       Lista timeouts ativos
>antievade clear @user                Limpa timeout ativo do user
```

📊 SCORE DE RISCO NO JOIN 🆕

```
>joinrisk                             Painel
>joinrisk on/off                      Ativar/desativar
>joinrisk threshold 70                Ajusta limite "avisar"
>joinrisk action 85 kick              Configura ação na faixa
>joinrisk action 95 ban               Configura ação na faixa
>joinrisk log #canal                  Canal de log
>joinrisk stats                       Últimos scores
>joinrisk clear                       Limpa cache
```

📈 STATS GRAPH 🆕

```
>statsgraph                           Gráfico padrão (30 dias)
>statsgraph 7                         Últimos 7 dias
>statsgraph 90                        Últimos 90 dias
>statsgraph auto on/off               Envio automático semanal
>statsgraph channel #canal            Canal de envio automático
```

🚨 ANTI-NUKE

```
>antinuke                             Abre PAINEL interativo (3 páginas)
>antinuke on/off                      Ativar/desativar
>antinuke punishment strip/ban/kick/lockdown
>antinuke threshold <n>               Nº de ações para punir
>antinuke threshold_whitelisted <n>   Threshold whitelisted (≥ normal + 1)
>antinuke whitelist_mode can_do_everything/confirm_kick
>antinuke window <segundos>           Janela de tempo
>antinuke globalthreshold <n>         Threshold global
>antinuke cooldown <s>                Cooldown entre punições
>antinuke log #canal                  Canal de logs
>antinuke webhook <URL>               Webhook de logs
>antinuke webhookremove               Remove webhook
>antinuke canal #canal                Restringe painel a um canal
>antinuke canal remove                Remove restrição
>antinuke lockdown on/off             Ativar/desativar lockdown
>antinuke minage <dias>               Idade mínima de conta
>antinuke jointhreshold <n>           Joins necessários
>antinuke joinwindow <s>              Janela
>antinuke raidaction kick/ban/verify  Ação em raid
>antinuke stats                       Estatísticas
>antinuke logs [n]                    Últimos logs
```

Hard limits:

```
>antinuke hardchannel <n>             Hard canais
>antinuke hardflood <n>               Hard msgs
>antinuke hardemoji <n>               Hard emojis
>antinuke hardcustomemoji <n>         Hard custom emojis
>antinuke hardspecial <n>             Hard caracteres especiais
>antinuke hardcaps <n>                Hard CAPS
>antinuke hardrepeated <n>            Hard repetidos
>antinuke hardzalgo <n>               Hard zalgo
```

Rollback:

```
>antinuke rollback on/off             Ativar/desativar
>antinuke rollbackmax <n>             Máx itens
>antinuke rollbackwindow <min>        Janela
>antinuke rollbacknow                 Forçar rollback
>antinuke rollbackstats               Estatísticas
>antinuke rollbackclear               Limpar cache
```

Whitelist:

```
>antinuke whitelist user @user        Adiciona user
>antinuke whitelist role @cargo       Adiciona cargo
>antinuke whitelist bot @bot          Adiciona bot
>antinuke whitelist check @alvo       Verifica
>antinuke whitelist list              Lista
>antinuke whitelist remove @x         Remove
```

Bot Approval:

```
>antinuke botapproval on/off          Ativar/desativar
>antinuke approver @user              Adiciona approver
>antinuke unapprover @user            Remove approver
>approvebot <id>                      Aprova bot
```

Canary Nuke:

```
>antinuke canary on/off               Ativar/desativar
>antinuke canary                      Status
>antinuke canarycancel                Cancelar vigilância
>antinuke canaryduration <s>          Duração
>antinuke canarytrigger <p>           Adiciona gatilho
>antinuke canaryremove <p>            Remove gatilho
```

🚪 ANTI-ESCALATION

```
>antiescalation                       Painel
>antiescalation on/off                Ativar/desativar
>antiescalation punishment revert/strip/ban/kick
>antiescalation autorevert on/off     Auto-revert
>antiescalation log #canal            Log
>antiescalation webhook <URL>         Webhook
>antiescalation block_self_promotion on/off
>antiescalation block_privileged_role_grant on/off
>antiescalation block_role_creation_dangerous on/off
>antiescalation block_role_edit_escalation on/off
>antiescalation block_everyone_edit on/off
>antiescalation block_position_abuse on/off
>antiescalation block_manage_roles_delegation on/off
>antiescalation block_cascade on/off
>antiescalation block_bot_admin_role on/off
>antiescalation block_admin_grant_to_bot on/off
>antiescalation protectrole @cargo    Cargo protegido
>antiescalation unprotectrole @cargo  Remove proteção
>antiescalation whitelist ...         Whitelist
>antiescalation cascadethreshold <n>  Threshold de cascata
>antiescalation massgrant <n>         Threshold de mass grant
>antiescalation risk <ação>           Simula score
>antiescalation stats                 Estatísticas
```

🎛️ PROTEÇÃO TOGGLE

```
>protecao                             Painel
>protecao off <modulo>                Desativa módulo
>protecao on <modulo>                 Ativa módulo
>protecao offall                      Desativa tudo
>protecao onall                       Ativa tudo
>protecao status                      Status
>protecao mode manual/auto            Modo
>protecao duration <min>              Duração auto
>protecao log #canal                  Log
>protecao notify on/off               Notificar owner
```

Módulos: antinuke, antiescalation, automod

🔐 CONFIRMAÇÃO VIA DM

```
>confirmconfig                        Painel
>cconf on/off                         Ativar/desativar
>cconf expires <min>                  Expiração
>cconf majority on/off                Requer maioria
>cconf log #canal                     Log
>pendingconfirms                      Confirmações pendentes
```

🤖 BOT WHITELIST PROTECTION

```
>botprotect                           Painel
>botprotect on/off                    Ativar/desativar
>botprotect modo threshold/never_kick Modo
>botprotect autokick on/off           Auto-kick
>botprotect log #canal                Log
>botprotect notify on/off             Notificar owner
>botprotect warn on/off               Warn sem permissão
>botprotect confirm addrole @cargo    Adiciona cargo confirmador
>botprotect confirm removerole @cargo Remove
>botprotect confirm adduser @user     Adiciona user
>botprotect confirm removeuser @user  Remove
>botprotect confirm exceptrole @cargo @user
>botprotect confirm exceptuser @admin @user
>botprotect confirm list              Lista
>botprotect test @bot                 Testa
```

🚨 RAID SUSPECTS

```
>raidconfig                           Painel
>raidconfig on/off                    Ativar/desativar
>raidconfig log #canal                Log
>raidconfig janela <min>              Janela de suspeita
>raidconfig retencao <min>            Retenção
>raidconfig threshold <n>             Threshold de mass join
>raidconfig window <s>                Janela de mass join
>raidconfig mention on/off            Mencionar suspeitos
>raidconfig showmsg on/off            Mostrar mensagens
>raidconfig notify on/off             Notificar owner
>raidconfig autodetect on/off         Auto-detectar raid
>raidconfig keyword add <palavra>     Adiciona palavra-chave
>raidconfig keyword remove <palavra>  Remove
>raidconfig keyword list              Lista
>raidlist                             Suspeitas recentes
>raidclear                            Limpar cache
>raidstats                            Estatísticas
```

⚠️ WARN SYSTEM

```
>wsys                                 Painel
>wsys on/off                          Ativar/desativar
>wsys autoapply on/off                Aplica direto
>wsys log #canal                      Log
>wsys janela <s>                      Janela de detecção
>wsys detect <regra> on/off           Ativa/desativa detecção
>wsys limite <regra> <n>              Ajusta limite
>wsys addpunish <avisos> <acao> [duração]
>wsys removepunish <avisos>           Remove punição
>wsys punicoes                         Lista punições
>wsys confirm addrole/removerole/adduser/removeuser @x
>wsys confirm exceptrole/exceptuser @x @y
>wsys immune addrole/removerole/exceptrole @x
>wsys aplicar addrole/removerole/adduser/removeuser @x
>warn2 @user <motivo>                 Aplicar warn
>warnbatch @u1 @u2 @u3 ...            Warn em lote (paralelo)
>warns2 @user                         Ver warns
>warninfo <warn_id>                   Detalhes
>clearwarns2 @user                    Limpar warns
>unwarn <warn_id>                     Remover warn específico
```

🔐 PERMISSÕES POR CATEGORIA

```
>perms                                Painel
>perms on/off                         Ativar/desativar
>perms categories                     Lista categorias
>perms category allow/deny @cargo <cat>
>perms category clear @cargo <cat>
>perms command allow/deny @cargo <cmd>
>perms command clear @cargo <cmd>
>perms user allow/deny @user <cmd>
>perms user clear @user <cmd>
>perms except add/remove @cargo @user <cat>
>perms list                           Lista regras
>perms check @user <cmd>              Verifica
>perms default allow/deny             Política padrão
>perms notify on/off                  Notificar user
>perms log #canal                     Log
>perms reset                          Resetar
```

🛡️ PUNIÇÕES

```
>kick @user [motivo]
>ban @user [motivo]
>softban @user [motivo]
>tempban @user <duração> [motivo]
>unban <user_id> [motivo]
>massban <id1> <id2> ...
>timeout @user <duração> [motivo]
>untimeout @user
>warn @user [motivo]
>warns @user
>clearwarns @user
>strike @user [motivo]
>unstrike @user
>silentban @user [motivo]
>silentkick @user [motivo]
>jail @user [motivo]
>unjail @user
>strip @user [motivo]
>restore @user
```

📁 MODERAÇÃO DE CANAIS

```
>lock [#canal]
>unlock [#canal]
>lockdown
>unlockdown
>hide [#canal]
>reveal [#canal]
>freeze [#canal]
>unfreeze [#canal]
>hideall
>showall
>slowmode <segundos>
>nsfw [#canal]
>sfw [#canal]
>clonechannel [#canal] [nome]
>templock <duração> [#canal]
```

💬 MODERAÇÃO DE MENSAGENS

```
>purge <n>
>purgebots <n>
>purgechannel
>nuke
>snipe [índice]
>editsnipe [índice]
```

🗑️ PURGE AVANÇADO

```
>purgelink <n>
>purgeimage <n>
>purgeattach <n>
>purgeregex <n> <pattern>
>purgeafter <msg_id>
>purgebefore <msg_id>
>purgemention <n>
>purgeembed <n>
>purgeinvite <n>
>purgebot [@bot] <n>
>purgeuser @user <n>
>purgepinguser @user <n>
>purgenuke <n>
>purgemulti <n> #c1 #c2 ...
>purgeage <tempo> <n>
>purgestats
```

👑 GESTÃO DE CARGOS

```
>addrole @user @cargo [motivo]
>removerole @user @cargo [motivo]
>temprole @user @cargo <duração> [motivo]
>createrole <nome> [cor] [motivo]
>deleterole @cargo [motivo]
>rolecolor @cargo <hex>
>roleinfo @cargo
>roleall @cargo
>roleunall @cargo
>dehoist <prefixo>
>rolepersist @user @cargo add/remove/list
```

🎤 MODERAÇÃO DE VOZ

```
>moveall <canal_origem> <canal_destino>
>disconnect @user
>voicekick @user
>voiceban @user <canal>
>voiceunban @user <canal>
>cloneroom [canal] [nome]
>vcmute @user
>vcunmute @user
```

🚫 PROTEÇÃO DE CONTEÚDO

```
>block <palavra>
>unblock <palavra>
>blocklist
>whitelist @user
>unwhitelist @user
```

📂 CASES / NOTES / APPEALS

```
>case <id>
>case <id> note <texto>
>case <id> close
>cases [@user]
>note add @user <texto>
>note list @user
>appeal <texto>
>appeal list
>appeal accept <id>
>appeal reject <id>
>note2 add @user <texto>
>note2 list @user
>note2 clear @user
>report @user <motivo>
```

📊 LOGS

```
>logs                                 Painel
>logs on/off                          Ativar/desativar
>logs canal #canal                    Canal padrão
>logs cor #ff0000                     Cor
>logs thumbnail <URL>                 Thumbnail
>logs thumbnailremove                 Remove thumbnail
>logs footer <texto>                  Footer
>logs footericon <URL>                Ícone do footer
>logs author <texto>                  Autor
>logs authoricon <URL>                Ícone do autor
>logs text <tipo> title/desc/color <valor>
>logs texts                           Lista textos
>logs context                         Painel de categorias
>logs context <cat> #canal            Define canal da categoria
>logs context <cat> remove            Remove
>logs categories                      Lista categorias
>logs test                            Log de teste
>logs reset                           Resetar

>eventlogs                            Painel
>eventlogs on/off                     Ativar/desativar
>eventlogs toggle <key>               Alterna um log
>eventlogs ignoreuser @user           Ignora user
>eventlogs unignoreuser @user         Remove
>eventlogs ignorechannel #canal       Ignora canal
>eventlogs reset                      Resetar

>modlogs [n]
>reason <case_id> <novo_motivo>
>audit [n]
```

📋 STAFF

```
>staff
>staffmetas
>staffadd <meta> [prova]
>staffranking
>staffhistory [@user]
>staffconfig
>staffmissionadd <key> <nome> <pontos> [requires_proof]
>staffmissionremove <key>
>staffgive @user <pontos> [motivo]
>staffremove @user <pontos> [motivo]
>staffauto <pontos> @cargo
>staffreset @user
```

⚙️ CONFIGURAÇÃO DO SERVIDOR

```
>setconfig <key> <valor>
>getconfig
>resetconfig [key]
>setperm @cargo/user <comando> <on/off>
>removeperm @cargo/user <comando>
>listperms @cargo/user
```

🎨 EMBED BUILDER

```
>embedbuilder                         Painel principal (alias: >eb)
>embedbuilder new                     Nova sessão
>embedbuilder edit <template>         Editar template
>embedbuilder import                  Importar JSON

>ebtemplates                          Lista templates (alias: >ebt)
>ebtemplates info <nome>              Detalhes + preview
>ebtemplates send <nome> #canal       Enviar em um canal
>ebtemplates sendmany <nome> #c1 #c2 #c3
>ebtemplates export <nome>            Exportar JSON
>ebtemplates delete <nome>            Deletar
>ebtemplates import <nome> <json>     Importar
>ebtemplates clonemany <origem> <n1> <n2>
>ebedit <nome>                        Abrir template no editor
```

🗂️ MOVER CANAIS/CATEGORIAS

```
>mover                                Painel de ajuda (alias: >mv)
>mover categoria <nome/ID> cima/baixo/top/bottom/<número>
>mover canal #canal cima/baixo/top/bottom/<número>
>mover canal #canal <categoria>       Muda de categoria
>mover listar                         Ver ordem atual com IDs
>mover top #canal                     Atalho
>mover bottom #canal                  Atalho
```

Importante: Categorias NÃO são mencionáveis com @. Use #nome, ID ou "Nome entre aspas".

✅ VERIFICATION GATE

```
>verification on/off
>verification channel #canal
>verification verifiedrole @cargo
>verification unverifiedrole @cargo
>verification method button/math/image/emojis/question
>verification attempts 3              Tentativas antes de kick
>verification timeout 120             Segundos pra resolver
>verification question "..."          Pergunta customizada
>verification difficulty easy/medium
>verification setup
>verification stats
```

⚡ OTIMIZADOR

```
>opt                                  Painel completo
>opt stats                            Stats do motor
>opt cache                            Ver caches registrados
>opt cache clear                      Limpar caches
>opt cache clean                      Limpar expirados
>opt snapshot                         Snapshot em RAM
>opt warm                             Forçar prefetch
>opt gc                               Forçar GC + limpar caches
>opt lag                              Loop lag atual
>opt tasks                            Background tasks
>opt routes                           Rate limit do Discord
>opt modules                          Patches aplicados
>opt report                           Relatório completo
```

🔧 UTILITÁRIOS

```
>embed <título> <descrição>
>announce <texto>
>poll <pergunta>
>say <texto>
```

📊 INFORMAÇÃO

```
>help [comando]
>ping
>status
>botinfo
>userinfo [@user]
>serverinfo
>avatar [@user]
>sync
```

🩺 SISTEMA

```
>health                               Health check completo
>mem                                  Uso detalhado de memória
>gc                                   Forçar garbage collection
```

---

🆕 PAINEL ANTI-RAID INTERATIVO (v1.5)

Comando: >antinuke (sem args)

Painel principal (3 páginas):

· Embed limpo com título + descrição + lista
· 3 páginas (25 + 25 + 14 itens) — depois da v13.5, com os 4 novos sub-painéis
· Botões de navegação: ◀️ Anterior / ▶️ Próximo
· Botões rápidos: 🔒 Lockdown | 📋 Whitelist | 🔐 Permissões | 📜 Histórico | 🗑️ Fechar
· Atualização em TEMPO REAL (edit_message)
· PERSISTENTE (sobrevive a restart)
· Cor verde/vermelho conforme estado
· Auto-refresh a cada 30s

Sub-menu de cada proteção:

· Estado atual (🟢/🔴)
· Threshold normal + threshold whitelisted (obrigatório ≥ normal + 1)
· Janela (quando aplicável)
· Hard limit (quando aplicável)
· Extra keys
· Select de ações: Ativar/Desativar, Configurar thresholds, Configurar janela, Configurar limite, Resetar
· Botão ⬅️ Voltar

🆕 Sub-painéis novos na v1.5:

· 🚫 Anti IP Logger (on/off, listar, add/remove, punish, log, whitelist)
· 🔄 Anti Evadir Mute (on/off, action, multiplier, log, list)
· 📊 Join Risk Score (on/off, threshold, 3 faixas de ação, log, stats)
· 📈 Stats Graph (dias padrão, canal de envio automático, toggle auto)

Correções na v1.5:

· Fix: botão Histórico quebrava quando o log tinha timestamp "naive" (sem timezone). Adicionado _normalize_timestamp() que converte tudo pra UTC com timezone.
· Fix: paginação ajustada pra 3 páginas (antes eram 2).

---

📊 COLEÇÕES MONGODB (~68)

Núcleo:

```
control_config, branding_config, guild_config,
role_permissions, user_permissions
```

Staff:

```
staff_config, staff_points, staff_actions, staff_auto_roles
```

Moderação:

```
warns, punishments, blocked_words, whitelist
```

Automod / Anti-Nuke:

```
automod_config, automod_infractions,
antinuke_config, antinuke_logs,
antinuke_panel_config
```

Anti-Escalation:

```
antiescalation_config, privilege_changes, privilege_alerts
```

Bot Approval:

```
pending_bots, bot_approvals
```

Warn System:

```
warn_system_config, warn_system_warns, warn_system_punicoes
```

Perm Categories:

```
perm_categories_config
```

Raid Suspects:

```
raid_suspects_config, raid_suspects_log, raid_detections
```

Protection Toggle:

```
protection_toggle_config, protection_toggle_logs
```

Bot Whitelist Protection:

```
bot_whitelist_protection_config
```

Whitelist Confirm:

```
whitelist_confirm_config
```

Cases / Notes / Appeals:

```
mod_cases, user_notes, appeals
```

Backup:

```
backups, backup_texts, backup_config
```

Rollback cache:

```
deleted_channels_cache, deleted_roles_cache
```

Logs:

```
logs_config, event_logs_config
```

Verification:

```
verification_config, verification_logs
```

Purge:

```
purge_logs
```

Jail / Strip / Notes Rápidas / Reports:

```
jail_saved, strip_saved, quick_notes, reports
```

Embed Builder:

```
embed_builder_sessions, embed_templates,
embed_panels, embed_messages, embed_variables,
embed_panel_logs, embed_lists
```

🆕 v13.5:

```
antilink_config, anti_evade_config, active_timeouts,
join_risk_config, join_risk_logs, stats_graph_config,
verification_v2_tokens, verification_v2_attempts
```

Total: ~68 coleções + ~115 índices + TTLs configurados

---

🔒 SEGURANÇA APLICADA

1. safe_object_id() centralizado em utils.py

Converte strings em ObjectId do MongoDB com segurança. Retorna None se inválido, em vez de crashar.

2. Rate Limiter por Usuário (Token Bucket)

Limita ações por usuário (ex: 3 comandos a cada 5 segundos).

3. TTLCache Thread-Safe (LRU)

Cache com tamanho máximo, tempo de vida e thread-safe.

4. Operações Atômicas com Retry

Decorador @retry_mongo(attempts=3) com backoff exponencial.

5. Validação de Inputs

· IDs validados via safe_object_id()
· Valores numéricos checados
· Menções parseadas com regex
· Canais/cargos verificados antes de usar

6. GuildGate

Centraliza controle de módulos/comandos/canal/manutenção. Cache TTL pra não bater no Mongo a cada mensagem.

7. Delegação entre cogs

Nunca registra o mesmo comando em 2 cogs. Use delegação (um registra, o outro expõe método público).

8. Views persistentes

Custom_id fixo + timeout=None.

🆕 9. Anti IP Logger

Lista de ~50 domínios de IP logger + comando pra adicionar/remover. Bloqueia antes da mensagem ser vista por membros.

🆕 10. Anti Evadir Mute

Persiste timeouts ativos. Reaplica quando o user volta. Configurável pra kick/ban se quiser mais agressivo.

🆕 11. Score de Risco no Join

Aditivo — NÃO substitui o sistema atual de raid. Só adiciona uma camada extra que roda em paralelo.

---

⚡ OTIMIZAÇÕES

Otimização Descrição Impacto
TTLCache com lock Thread-safe, TTL e LRU -90% queries MongoDB
Pool MongoDB max 20 Railway free friendly -80% conexões abertas
GC Threshold (700, 10, 10) Calibrado -50% overhead GC
Memory Monitor Auto-GC quando RAM > 380MB Evita OOM no Railway
Batch Updates $set agrupado -70% queries
Índices + TTL Criados no startup -50% tamanho DB
max_messages=200 Cache interno menor -50% RAM discord.py
Shutdown graceful Fecha tasks corretamente Sem leaks
Cog load idempotente Unload antes de load Zero crash em restart
Sync 429 tolerante Ignora rate limit Bot continua
Rate limiter token bucket Sem dict infinito Anti-spam leve
Cache por guild no embeds Cor/footer sem query -95% no Mongo
Cache de config por Cog Cada Cog tem o seu -80% queries
Verificação módulo ANTES Early return no on_message -70% CPU com módulos off
run_parallel() Executa N coisas em paralelo 4-6x mais rápido
get_config_fresh() Lê direto do banco (painel) Sempre atual
Auto-refresh painel Atualiza a cada 30s Outros admins veem mudanças
Paginação painel 3 páginas de 25+25+14 Todas as opções visíveis
🆕 Pillow para gráficos Gera PNG em background Não trava o bot
🆕 Captcha em background Imagem gerada em thread Não trava o loop

---

📈 MÉTRICAS DE PERFORMANCE

Métrica Valor Esperado
Cache Hit Rate 85-95%
Memória RAM ~75 MB
Conexões MongoDB 20 (pool)
Response Time < 300ms
Loop lag < 100ms
Uptime 24/7
GC Frequency A cada 5 min
GC Forçado Só se RAM > 380MB
Índices MongoDB ~115 (com TTL)
Cogs carregados 31 (15 obrig + 16 opc)
Comandos ~260
Comandos pesados 4-6x mais rápidos via run_parallel

---

⚠️ ERROS COMUNS E SOLUÇÕES

Problema Solução
SSL handshake Verificar IP no Atlas (0.0.0.0/0)
IP bloqueado Adicionar 0.0.0.0/0 no Atlas
Slash 429 Normal em restart seguido; bot continua
Cog already loaded Setup idempotente + unload antes do load
ObjectId inválido safe_object_id() valida antes
Rate limit global Token bucket por usuário
Módulo desativado bloqueando Admin usa >controltoggle pra ativar
Backup muito grande Ajustar BACKUP_MAX_PER_GUILD
Anti-nuke punindo admin Adicionar em >antinuke whitelist @user
Automod punindo staff Adicionar cargo em >automod whitelist @cargo
CommandRegistrationError Nunca registrar o mesmo comando em 2 cogs
Categoria não menciona Usar #nome, ID ou "Nome entre aspas"
f-string aninhada Não usar f"{f'...'}"; extrair pra variável
Painel desatualizado Auto-refresh de 30s + get_config_fresh()
Threshold whitelisted errado Deve ser ≥ normal + 1
🆕 Pillow não instalado Instale com pip install Pillow
🆕 Histórico bugado (naive datetime) Corrigido na v1.5 do painel
🆕 Captcha não gera imagem Verificar Pillow + fontes do sistema

---

📌 LIÇÕES APRENDIDAS

Bugs corrigidos nas versões anteriores:

1. CategoryChannel.topic — commands_event_logs.py tentava acessar .topic em categorias. Fix: detecta tipo antes.
2. Nome do arquivo com typo — renomeado + main.py alinhado.
3. main.py pedindo cog inexistente — alinhado.
4. IndentationError no ebtemplates — v1.4 limpou.
5. f-strings aninhadas — extraído pra variáveis.
6. Paralelismo (v12.6) — run_parallel() no utils.py.
7. CommandRegistrationError (v13.1) — delegação.
8. KeyError: limite_links (v13.1) — adicionadas no DEFAULT_CONFIG.
9. Cache desatualizado no painel (v13.2) — get_config_fresh().
10. Painel não atualizava sozinho (v13.2) — auto-refresh de 30s.
11. Threshold whitelisted não configurável (v13.3) — chaves {key}_whitelisted.
12. Lockdown/Whitelist/Histórico poluindo select (v13.3) — botões dedicados.
13. Lockdown não removia exceções de cargos (v13.3) — lógica em _apply_lockdown().
14. 12 proteções cortadas do select (v1.4) — paginação.
15. 🆕 Histórico bugado (v1.5) — timestamp naive vs aware. Fix: _normalize_timestamp().

Regras de ouro:

· ⚠️ Nome do arquivo ≠ nome no main.py → "could not be loaded or found".
· ⚠️ Categorias não são mencionáveis com @ — usar #nome, ID ou nome entre aspas.
· ⚠️ Event listeners — checar isinstance antes de acessar atributos específicos.
· ⚠️ f-strings aninhadas — não usar f"{f'...'}"; extrair pra variável.
· ⚠️ Comandos em massa — sempre usar run_parallel().
· ⚠️ Nunca registrar o mesmo comando em 2 cogs.
· ⚠️ Views persistentes — custom_id fixo + timeout=None.
· ⚠️ Painéis interativos — interaction.response.edit_message().
· ⚠️ Config que precisa ser sempre atual — get_config_fresh().
· ⚠️ Modal do Discord não aceita @ — usar ID puro.
· ⚠️ Threshold whitelisted sempre ≥ normal + 1.
· ⚠️ Lockdown é emergência — sem confirmação.
· ⚠️ Painel com muitas opções — usar paginação.
· 🆕 ⚠️ Datetime naive vs aware — SEMPRE usar timezone.utc em logs.
· 🆕 ⚠️ Pillow trava o loop — sempre gerar imagens em thread separada.

---

📜 LICENÇA

Projeto privado — uso interno da comunidade GT - Stumble Guys.

---

👑 AUTOR

Dark e Smokezinn

· 📺 YouTube: https://youtube.com/@dark-e-smokezinn
· 🎮 Comunidade GT - Stumble Guys

---

Versão: 13.5
Última atualização: 2026
Foco: Moderação, proteção e performance
Total de arquivos: 41
Cogs carregados: 31 (15 obrigatórios + 16 opcionais)
Comandos: ~260

```

---

✅ **Mensagem 2 concluída.** Salve os dois arquivos (`requirements.txt` e `README.md`).

**Não suba o bot ainda.** Os dois arquivos sozinhos não fazem nada — o `README.md` é só documentação, e o `requirements.txt` só vai adicionar o Pillow quando você reinstalar as dependências.

**Próxima mensagem (3):** `commands_antilink_protection.py` — o **Anti IP Logger** completo.

Quando quiser, é só dizer **"próximo"**.

### Whitelist automática do responsável
Defina `OWNER_USER_ID` com seu ID numérico do Discord. O bot verifica cada servidor em que está e só adiciona esse usuário à whitelist humana do Anti-Nuke quando confirmar que ele é membro daquele servidor. Se não estiver no servidor, nenhuma entrada é criada.


### Segurança — configuração final
- Habilite **Server Members Intent** no Discord Developer Portal.
- Defina `OWNER_USER_ID` com seu ID numérico. A conta só é incluída na whitelist humana do Anti-Nuke se o bot confirmar que ela está no servidor.
- Join Risk inicia em modo de observação (`dry_run=true`) para servidores sem configuração salva.
- Consulte `AUDIT_FINAL_PRE_PRODUCTION.md` antes de homologar.


## Migração assíncrona do MongoDB — etapa 4

O fluxo de pontuação, histórico, ranking e cargos automáticos da Staff foi migrado para Motor. Consulte `AUDIT_ASYNC_MONGODB_ETAPA4.md`. A migração global ainda está em andamento; não implante como produção antes da homologação.


## Migração assíncrona do MongoDB — etapa 6

O módulo `commands_event_logs.py` agora usa Motor para carregar, atualizar e resetar as configurações de logs de eventos. Todos os chamadores locais foram atualizados para `await`. A migração do restante do projeto continua; consulte `AUDIT_ASYNC_MONGODB_ETAPA6.md`.

## Etapa 7 — sistema de logs
O `LogsManager` e seus consumidores foram migrados para Motor/`await`; ver `AUDIT_ASYNC_MONGODB_ETAPA7.md`.

## Etapa 8 — proteção de bots na whitelist
- `BotWhitelistManager` migrado para Motor; consumidores aguardam as operações.


## Migração assíncrona do MongoDB — etapa 9

O sistema de avisos (`commands_warn_system.py`) foi migrado para Motor, incluindo configuração, cleanup, aplicação, consulta e remoção de avisos. Restam 153 referências textuais síncronas no projeto; a migração ainda não terminou. Consulte `AUDIT_ASYNC_MONGODB_ETAPA9.md`.


### Migração assíncrona do MongoDB — etapa 10
O módulo de backups foi migrado para Motor. Consulte `AUDIT_ASYNC_MONGODB_ETAPA10.md` para escopo, verificações estáticas e limitações.

## Migração MongoDB — Etapa 11

O módulo `commands_moderation_extra.py` foi migrado para Motor. Os comandos e listeners existentes foram preservados; consulte `AUDIT_ASYNC_MONGODB_ETAPA11.md` para o escopo e as verificações.


## Migração assíncrona do MongoDB — Etapa 18

Os módulos Anti-Escalation e Protection Toggle foram migrados para Motor. Os comandos e regras existentes foram preservados e os consumidores locais passaram a aguardar as operações assíncronas. Restam 89 chamadas síncronas executáveis no projeto; consulte `AUDIT_ASYNC_MONGODB_ETAPA18.md`. A validação ainda é estática e não certifica produção.
