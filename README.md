# Расписание ДГТУ для Home Assistant

Приложение для Home Assistant OS. На главном экране — пары на сегодня, оттуда же открываются завтра и любой другой день. Источник тот же, что у [Telegram-бота расписания](https://github.com/Litukrusa/DGTU_Raspisanie): публичное API `edu.donstu.ru` и `edu-tpi.donstu.ru`. Токен бота и MongoDB не нужны, достаточно имени группы или фамилии преподавателя.

[![Открыть добавление репозитория в Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FLitukrusa%2Fha-dgtu-raspisanie)

## Установка из терминала

В терминале Home Assistant:

```bash
ha store repositories add https://github.com/Litukrusa/ha-dgtu-raspisanie
ha apps install 69ec2b06_dgtu_raspisanie
ha apps start 69ec2b06_dgtu_raspisanie
```

Дальше в конфигурации приложения укажите университет (`ДГТУ` или `ПИ`), группу или преподавателя и точное имя, сохраните и перезапустите. Откройте **Расписание** в боковой панели и нажмите **На главный экран** — на обзоре появится карточка на сегодня, с переключением на завтра и другие дни.

Подробности, подгруппа и карточка на обзоре — в [документации приложения](dgtu_raspisanie/DOCS.md).

Приложение рассчитано на 64-битный Home Assistant OS (`aarch64` и `amd64`).
