// Optional Telegram mirror. Separate settings card: no bot token ever round-trips to the UI.
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Text;
using System.Windows.Forms;

namespace CodexAutoResume
{
    internal sealed partial class SettingsForm
    {
        private CheckBox telegramOn;
        private TextBox telegramToken;
        private TextBox telegramChat;
        private Label telegramFeedback;
        private Button telegramSave;
        private Button telegramTest;

        private void BuildTelegram(TableLayoutPanel stack)
        {
            TableLayoutPanel card = NewGroup("Telegram-бот", stack);
            card.Controls.Add(HelpText(
                "Telegram получает события Codex независимо от всплывающих уведомлений Windows. " +
                "Бот запускается вместе с монитором и не требует отдельного запуска."));
            telegramOn = NewCheck("Отправлять уведомления в Telegram", false, false);
            telegramOn.Enabled = false;
            card.Controls.Add(telegramOn);

            telegramToken = new TextBox();
            telegramToken.Width = Px(290);
            telegramToken.UseSystemPasswordChar = true;
            telegramToken.BackColor = Card;
            telegramToken.ForeColor = Ink;
            telegramToken.BorderStyle = BorderStyle.FixedSingle;
            telegramToken.AccessibleName = "Токен Telegram-бота";
            card.Controls.Add(NewRow("Токен бота (пусто — оставить прежний)", telegramToken));

            telegramChat = new TextBox();
            telegramChat.Width = Px(290);
            telegramChat.BackColor = Card;
            telegramChat.ForeColor = Ink;
            telegramChat.BorderStyle = BorderStyle.FixedSingle;
            telegramChat.AccessibleName = "Telegram Chat ID";
            card.Controls.Add(NewRow("Chat ID пользователя, группы или канала", telegramChat));
            card.Controls.Add(HelpText(
                "Бот должен иметь право отправлять сообщения в выбранный чат. " +
                "Токен хранится отдельно, зашифрован средствами Windows. " +
                "Тексты задач и ответы Codex в Telegram не передаются."));

            telegramFeedback = HelpText("Настройки Telegram загружаются автоматически…");
            card.Controls.Add(telegramFeedback);
            var actions = new FlowLayoutPanel();
            actions.AutoSize = true;
            actions.AutoSizeMode = AutoSizeMode.GrowAndShrink;
            actions.Dock = DockStyle.Fill;
            actions.FlowDirection = FlowDirection.LeftToRight;

            telegramSave = MakeButton("Сохранить Telegram", false, delegate { SaveTelegram(); });
            telegramTest = MakeButton("Проверить связь", false, delegate { TestTelegram(); });
            telegramSave.Enabled = telegramTest.Enabled = false;
            actions.Controls.Add(telegramSave);
            actions.Controls.Add(telegramTest);
            card.Controls.Add(actions);

        }

        private void LoadTelegram()
        {
            telegramFeedback.Text = "Чтение сохранённых настроек Telegram…";
            CallAsync("telegram-status", null, delegate(Dictionary<string, object> reply)
            {
                if (!Ok(reply))
                {
                    telegramFeedback.Text = "Не удалось прочитать настройки Telegram.";
                    return;
                }
                var data = Map(reply, "result");
                if (data == null)
                {
                    telegramFeedback.Text = "Не удалось получить состояние Telegram.";
                    return;
                }
                telegramOn.Checked = Equals(Get(data, "enabled"), true);
                telegramChat.Text = Str(data, "chat_id") ?? "";
                bool configured = Equals(Get(data, "configured"), true);
                bool ready = Equals(Get(data, "credentials_ready"), true);
                telegramFeedback.Text = !configured ? "Токен пока не задан." : !ready
                    ? "Сохранённый токен недоступен. Укажи токен заново и сохрани." : telegramOn.Checked
                    ? "Telegram включён. Данные загружены; монитор отправляет события автоматически."
                    : "Токен сохранён. Включи Telegram, чтобы получать уведомления.";
                telegramOn.Enabled = telegramSave.Enabled = telegramTest.Enabled = true;
            });
        }

        private void SaveTelegram()
        {
            var payload = new StringBuilder("{\"enabled\":").Append(telegramOn.Checked ? "true" : "false");
            payload.Append(",\"chat_id\":").Append(Json.Escape(telegramChat.Text.Trim()));
            if (telegramToken.Text.Length > 0)
                payload.Append(",\"token\":").Append(Json.Escape(telegramToken.Text));
            payload.Append("}");
            telegramSave.Enabled = telegramTest.Enabled = false;
            telegramFeedback.Text = "Сохранение…";
            CallAsync("telegram-update", payload.ToString(), delegate(Dictionary<string, object> reply)
            {
                telegramSave.Enabled = telegramTest.Enabled = true;
                if (!Ok(reply))
                {
                    telegramFeedback.Text = "Не удалось сохранить: " +
                        (Str(reply, "error") ?? "неизвестная ошибка");
                    return;
                }
                telegramToken.Clear();
                telegramFeedback.Text = "Настройки Telegram сохранены.";
            });
        }

        private void TestTelegram()
        {
            telegramTest.Enabled = false;
            telegramFeedback.Text = "Отправка проверочного сообщения…";
            CallAsync("telegram-test", null, delegate(Dictionary<string, object> reply)
            {
                telegramTest.Enabled = true;
                telegramFeedback.Text = Ok(reply) ? "Проверочное сообщение отправлено в Telegram."
                    : "Проверка не удалась. Сначала сохрани токен и Chat ID; проверь права бота.";
            });
        }
    }
}
