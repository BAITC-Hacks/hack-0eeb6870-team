# Подключение NVIDIA Brev

Для Brev используется свой OpenAI-compatible сервер, а не ключ NVIDIA API Catalog.

## Развёрнутая конфигурация

- Контейнер: `vllm/vllm-openai:v0.12.0`.
- Модель: `Qwen/Qwen2.5-7B-Instruct`.
- Имя модели в API: `moneygraph-qwen`.
- Одна GPU, device 0, память до 70%, контекст 8192 токена, до 2 одновременных последовательностей.
- Tool calling: Hermes, `--enable-auto-tool-choice --tool-call-parser hermes`.
- Порт: только `127.0.0.1:8000` на сервере. Публичный доступ не включён.
- Контейнер `moneygraph-llm`, логи `~/moneygraph-deploy/server.log`.

Команда запуска на Brev:

```bash
mkdir -p ~/moneygraph-deploy/cache
docker run -d --name moneygraph-llm --gpus device=0 --ipc=host \
  -p 127.0.0.1:8000:8000 \
  -v $HOME/moneygraph-deploy/cache:/root/.cache/huggingface \
  vllm/vllm-openai:v0.12.0 \
  --model Qwen/Qwen2.5-7B-Instruct --served-model-name moneygraph-qwen \
  --max-model-len 8192 --max-num-seqs 2 --gpu-memory-utilization 0.70 \
  --enforce-eager --enable-auto-tool-choice --tool-call-parser hermes \
  --disable-log-requests
```

Если контейнер уже существует, не создавайте второй. Статус: `docker ps -a --filter name=moneygraph-llm`. Логи: `docker logs --tail 30 moneygraph-llm`. Проверка API на сервере: `curl http://127.0.0.1:8000/v1/models`. Остановка модели: `docker stop moneygraph-llm`; повторный запуск: `docker start moneygraph-llm`. Остановка контейнера не останавливает оплачиваемый инстанс Brev.

## Соединение с компьютером

Нужен действующий SSH-доступ к этому серверу. Адрес Jupyter с браузерной авторизацией сам по себе не является API модели и не заменяет SSH-доступ.

На компьютере с установленным и авторизованным Brev CLI:

```bash
brev port-forward ИМЯ_ВАШЕГО_ИНСТАНСА --port 18000:8000
```

Либо используйте SSH-команду из Brev, добавив `-N -L 127.0.0.1:18000:127.0.0.1:8000 -o ExitOnForwardFailure=yes` и сохранив её параметры пользователя, адреса, порта и ключа. Не подставляйте URL Jupyter вместо SSH-host.

Оставьте туннель работающим. На локальном компьютере `http://127.0.0.1:18000/v1/models` должен отвечать списком с `moneygraph-qwen`.

Скопируйте `.env.brev.example` в `.env` рядом с run.py:

```dotenv
NVIDIA_BASE_URL=http://127.0.0.1:18000/v1
NVIDIA_MODEL=moneygraph-qwen
NVIDIA_API_KEY=
NVIDIA_MAX_CALLS=40
```

Перезапустите MoneyGraph и включите LLM. Пустой ключ разрешён только для локального адреса — в этом варианте доступ обеспечивает SSH. Удалённый адрес требует HTTPS и API-ключ. При обрыве туннеля ассистент вернётся к локальным шаблонам. Таймаут запроса — 90 секунд, один запрос на вопрос. Число вызовов не ограничивает стоимость GPU-инстанса.

Qwen2.5-7B — компактная модель для одной L4. Она выбирает инструменты; числа и объяснения формируются локальным кодом из результатов. В сложных вопросах возможны ошибки выбора функции — проверяйте аргументы. Расчёт графа и CSV от неё не зависят.

Официальные инструкции: [Brev connectivity](https://docs.nvidia.com/brev/cli/connectivity), [vLLM tool calling](https://docs.vllm.ai/en/v0.12.0/features/tool_calling/).



На Windows можно использовать start-brev-tunnel.ps1 с параметрами -SshHost, -SshUser, -SshPort, -KeyPath и -KnownHostsPath. Адрес и порт берутся из карточки Brev; known_hosts должен содержать предварительно проверенный ключ сервера. SSH-ключи и данные конкретного инстанса в репозиторий не входят.
