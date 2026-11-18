# GT Bot — Clean Fixed (revisão estática)

## Base e preservação
- Base estrutural: `GT Bot.zip` (original).
- Mantidos os 42 arquivos da distribuição original; nenhum módulo funcional foi removido.
- Os módulos independentes de Anti-Evade, Anti-Escalation, Anti-Link, AutoMod e Join Risk foram mantidos como no original nesta entrega. Isso evita mudanças globais de punição, thresholds ou cooldowns sem validação.
- Alterações concentradas em `commands_antinuke.py`, `commands_antinuke_panel.py` e `commands_warn_system.py`.

## Alterações aplicadas

### Anti-Nuke (`commands_antinuke.py`)
- Revalidação da configuração diretamente no MongoDB imediatamente antes da punição de humanos.
- Falha na leitura da configuração resulta em não aplicar punição automática.
- A revalidação confirma que o Anti-Nuke continua ligado, que a proteção geral de usuários continua ligada e que a proteção específica que detectou a ação continua habilitada.
- O toggle `user_protection_enabled` não desliga a defesa contra bots: bots continuam pelo caminho de proteção próprio.
- Executor que não pode ser confirmado como membro do servidor não é punido automaticamente.
- No Anti-Nuke, humanos recebem no máximo kick/remoção de cargos; banimento de bot malicioso permanece no caminho de bots.
- Anti-Raid/lockdown agora respeita `user_protection_enabled` e revalida a configuração antes de expulsar um humano.
- Canary Nuke só pode ser armado por owner/manager autorizado; usuários comuns não conseguem ativar a janela de punição digitando uma palavra no chat.
- No Canary, bots continuam sujeitos a ban; humanos são tratados pelo caminho de kick e respeitam o toggle/imunidades.
- Whitelist normaliza IDs inteiros e textuais, evitando falhas por diferenças de tipo vindas de dados antigos do MongoDB.
- Adicionadas operações atômicas de inclusão/remoção na whitelist para evitar sobrescrita de atualizações concorrentes.
- Comandos legados de aprovação/configuração por texto foram desregistrados/bloqueados; o painel embed é o caminho de configuração.
- Thresholds originais (incluindo valores padrão 3/6 do caminho geral) foram preservados; não houve aumento de limites/cooldowns.

### Painel (`commands_antinuke_panel.py`)
- Atualizações de configuração passam pelo manager e invalidam o cache.
- Views do Anti-Nuke/whitelist/permissões validam a permissão de quem interage.
- A regra de acesso ao painel foi alinhada com owner, managers e a opção `antinuke_require_manager`.
- A consulta de whitelist normaliza IDs para evitar exibir estado incorreto quando dados antigos estão como texto.
- O subcomando textual `antinuke canal` foi desregistrado, pois a configuração do Anti-Nuke deve ser pelo embed.

### Avisos (`commands_warn_system.py`)
- Avisos de infrações detectadas são enviados no canal onde a mensagem ocorreu; não são enviados por DM.
- O canal é propagado também no fluxo de avisos em lote.
- Se não for possível entregar o aviso, a punição escalonada não é executada silenciosamente; o registro do aviso permanece para auditoria.
- Espaços comuns, tabs e quebras de linha não são tratados como caracteres invisíveis.
- O detector não marca ZWJ/ZWNJ como invisíveis, evitando falsos positivos em sequências de emoji e texto que usa esses caracteres.

## Validação realizada
- Compilação sintática de todos os módulos Python (`py_compile`).
- Inspeção estática dos caminhos de punição e dos comandos registrados do Anti-Nuke.
- Conferência de que o pacote contém todos os arquivos da distribuição original.

## Limitações importantes
- Esta é uma auditoria estática, não uma garantia de comportamento em produção.
- Não foi possível importar/executar o bot neste ambiente porque as dependências do projeto (incluindo `discord.py` e `pymongo`) não estão instaladas.
- Não foram executados testes com Discord real, MongoDB real, permissões/hierarquia reais, nem testes de concorrência/restart.
- Antes de produção, validar em servidor de teste: toggle de proteção humana OFF, whitelist add/remove, painel após restart, bot malicioso, usuário comum, manager, admin, owner, Anti-Raid, Canary, falha de Mongo e ausência de permissões de envio de avisos.
