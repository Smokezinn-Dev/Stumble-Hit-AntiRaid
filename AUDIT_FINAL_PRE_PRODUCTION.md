# Auditoria de correções — Stumble Hit AntiRaid

**Data:** 17/11/2026  
**Base:** pacote dual release entregue anteriormente.  
**Escopo:** revisão estática e correções de segurança/robustez descritas neste documento.

## Correções aplicadas

1. **Intents:** `intents.members=True`. É obrigatório também ativar *Server Members Intent* no Discord Developer Portal da aplicação.
2. **Permissões slash/context menu:** adicionadas verificações de permissão em tempo de execução para os comandos de moderação que declaram permissões padrão; verificações de hierarquia e bloqueio de auto-punição/alvo dono do servidor nos caminhos de kick/ban/timeout.
3. **Whitelist humana:** confirmação passou a falhar fechada. Confirmação desativada, ação não configurada, ausência do cog ou falha no envio de DMs não executam a punição. Anti-Nuke não pune membro whitelisted se o cog de confirmação não estiver disponível.
4. **Anti-Nuke:** ação é recalculada a partir da configuração atual após a confirmação. Ban automático de humanos continua convertido em kick, preservando a regra existente.
5. **Join Risk:** ação e faixa são recalculadas após a leitura fresca da configuração; `dry_run` padrão passou a `True` para novos servidores sem configuração, exigindo habilitação consciente das ações automáticas. Configurações já salvas no MongoDB não são sobrescritas.
6. **Toggle de proteções:** estado desligado e prazo passam a ser persistidos em `active_protection_toggles`, restaurados no startup e reaplicados caso o processo caia no meio da operação. Se o banco não aceitar a persistência, o toggle não desliga a proteção. A task só remove o toggle da memória depois de confirmar a restauração.
7. **Responsável do bot:** nova variável `OWNER_USER_ID`. O bot verifica a presença do ID em cada servidor e adiciona a conta à whitelist humana do Anti-Nuke apenas se conseguir confirmar que ela é membro. Também verifica quando o responsável entra depois que o bot já está online. Conta bot não é adicionada.
8. **Configurações globais:** `ANTIRAID_ENABLED` agora controla os caminhos de Anti-Raid/Join Risk; `AUTOMOD_ENABLED` controla o listener do AutoMod; `CACHE_TTL_DEFAULT` e `CACHE_MAX_SIZE_DEFAULT` passam a controlar instâncias de `TTLCache` que não definem valores explícitos. `BOT_APPROVAL_TIMEOUT` agora controla o tempo padrão de confirmação de bot whitelisted (valor do ambiente em segundos; painel em minutos).
9. **MongoDB:** removido o `ping` síncrono no primeiro acesso PyMongo e reduzidos timeouts da conexão síncrona; retry Motor usa `await asyncio.sleep()` em vez de `time.sleep()`.
10. **DB_NAME:** leitura de configuração no `main.py` agora usa `get_connection()` e respeita `DB_NAME`.

## Verificações estáticas executadas

- Os 39 módulos Python de cada versão foram analisados com `ast.parse`: **0 erros de sintaxe**.
- Os comandos slash/context menu com permissões padrão foram verificados: **todos têm check de permissão em tempo de execução**.
- As duas cópias foram comparadas; estrutura e lógica correspondem após normalização da marca.
- O pacote ZIP foi reaberto e `ZipFile.testzip()` confirmou integridade.
- Nenhum token Discord ou segredo real foi incluído.

## Limitações que ainda impedem uma aprovação definitiva para produção

- **MongoDB síncrono:** ainda existem cerca de 211 chamadas `get_connection()` espalhadas pelos módulos legados. Embora a conexão inicial não faça mais `ping` bloqueante, operações PyMongo continuam síncronas e podem bloquear o event loop durante lentidão/indisponibilidade do banco. A migração completa para Motor ou isolamento de cada operação síncrona em threads exige uma etapa própria e testes de regressão; não foi feita automaticamente para evitar quebrar o comportamento dos módulos.
- **Integração real:** não foram executados testes com tokens Discord, servidor real, MongoDB real, rate limits, hierarquia real de cargos, auditoria Discord ou ciclos de reinicialização. As dependências de runtime não estavam disponíveis para iniciar os bots neste ambiente.
- **Intents no portal:** habilitar `Server Members Intent` manualmente para cada aplicação além da alteração no código.
- **OWNER_USER_ID:** preencher a variável separadamente nas duas hospedagens com o ID numérico correto. Enquanto estiver vazia ou o usuário não for membro do servidor, nada será adicionado.
- **Join Risk existente:** a configuração já gravada no MongoDB é preservada. Se o servidor já tiver `dry_run=false`, a mudança do padrão não altera esse valor; revise conscientemente o painel antes de ativar punições automáticas.

## Estado recomendado

**Apto para testes de homologação, não certificado para produção.** Faça os testes da checklist abaixo em servidores de teste separados antes de ativar as ações automáticas.

## Checklist de homologação

- [ ] Ativar Server Members Intent em ambas as aplicações.
- [ ] Definir `OWNER_USER_ID` nas duas hospedagens e confirmar inclusão apenas nos servidores em que o ID é membro.
- [ ] Testar `/kick`, `/ban`, `/timeout`, `/warn`, `/purge`, `/lock` e `/unlock` com usuário sem permissão e com permissão.
- [ ] Testar tentativa de punir a si mesmo, dono do servidor e cargo igual/superior.
- [ ] Testar whitelist humana com DM disponível, DM bloqueada, confirmação desativada e cog indisponível: nenhuma falha deve resultar em punição automática.
- [ ] Alterar Join Risk para `dry_run=true`, observar logs, calibrar thresholds e só então decidir se ativa ações automáticas.
- [ ] Desligar proteção por tempo, reiniciar o bot, confirmar que o estado continua e que a reativação ocorre no prazo.
- [ ] Simular MongoDB lento/indisponível e medir heartbeat, latência de eventos e interações.
- [ ] Testar bot adicionado por humano whitelisted e não whitelisted, com auditoria atrasada/indisponível.

---

Esta auditoria é estática e não substitui testes de integração nem uma revisão independente de segurança.
