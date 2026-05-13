from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

from config.settings import (
    find_settings_path,
    get_settings_search_paths,
    save_settings,
)
from gui.main_window import MainWindow


ARTICLE_DB_FOLDER_NAME = "Article Database"
NEW_FOLDER_NAME = "!New"


def _ensure_article_db_structure(project_home: Path) -> None:
    """
    Гарантирует, что в project_home существует подпапка 'Article Database/!New'.
    Если её нет — создаёт.
    """
    article_db = project_home / ARTICLE_DB_FOLDER_NAME
    new_dir = article_db / NEW_FOLDER_NAME
    new_dir.mkdir(parents=True, exist_ok=True)


def _resolve_project_home(chosen: Path) -> Path | None:
    """
    Помогает пользователю выбрать правильный PROJECT_HOME_DIR.

    Логика:
      A. Если выбран каталог с именем "Article Database" — это, скорее всего,
         сама папка статей. Предложить родительский каталог.
      B. Если в выбранном каталоге уже есть подпапка "Article Database" — отлично,
         используем как есть (типичный случай при повторном запуске).
      C. Если ни (A), ни (B) — выбранная папка пустая или просто новая.
         Спрашиваем подтверждение, что в ней будет создана подпапка "Article Database".

    Возвращает финальный путь PROJECT_HOME_DIR или None, если пользователь отменил.
    """
    chosen = chosen.resolve()

    # A. Пользователь выбрал саму папку "Article Database"
    if chosen.name == ARTICLE_DB_FOLDER_NAME:
        parent = chosen.parent
        ok = messagebox.askyesno(
            "First-time setup",
            f"You selected the folder:\n  {chosen}\n\n"
            f"It looks like this IS the 'Article Database' folder itself. "
            f"The application expects you to point to its PARENT folder, where "
            f"'Article Database/', 'Contents/', 'PDF_summaries/' and "
            f"'article_index.db' will all live side by side.\n\n"
            f"Use the parent folder instead?\n  {parent}",
            icon="question",
        )
        if ok:
            return parent
        # Пользователь сказал "нет" — даём ему ещё раз выбрать
        return None

    # B. В выбранной папке уже есть "Article Database" — отлично
    if (chosen / ARTICLE_DB_FOLDER_NAME).is_dir():
        return chosen

    # C. Пустая/новая папка — создадим в ней структуру
    ok = messagebox.askyesno(
        "First-time setup",
        f"The selected folder does not contain an '{ARTICLE_DB_FOLDER_NAME}' subfolder:\n"
        f"  {chosen}\n\n"
        f"The application will create the following structure:\n"
        f"  {chosen / ARTICLE_DB_FOLDER_NAME}\n"
        f"  {chosen / ARTICLE_DB_FOLDER_NAME / NEW_FOLDER_NAME}\n\n"
        f"Use this folder as PROJECT_HOME_DIR?",
        icon="question",
    )
    if ok:
        return chosen
    return None


def _run_first_run_wizard(root: tk.Tk) -> bool:
    """
    Запускается при первом старте приложения, когда settings.json не найден.
    Просит пользователя выбрать PROJECT_HOME_DIR и сохраняет настройки.

    Возвращает True, если настройки успешно созданы; False — если пользователь
    отменил выбор (приложение должно завершиться).
    """
    searched_paths = "\n".join(f"  • {p}" for p in get_settings_search_paths())

    messagebox.showinfo(
        "First-time setup",
        "Welcome to Brain Worm!\n\n"
        "Settings file was not found in any of the expected locations:\n\n"
        f"{searched_paths}\n\n"
        "On the next step, please select a ROOT folder where the application "
        "will keep everything: an 'Article Database/' subfolder with your PDFs, "
        "a 'Contents/' subfolder for extracted JSON, a 'PDF_summaries/' subfolder "
        "for DOCX summaries, and the SQLite database file.\n\n"
        "Tip: do NOT select the 'Article Database' folder itself — pick its "
        "PARENT folder.",
    )

    while True:
        chosen_str = filedialog.askdirectory(
            parent=root,
            title="Select ROOT folder (PARENT of 'Article Database')",
            mustexist=True,
        )

        if not chosen_str:
            again = messagebox.askretrycancel(
                "First-time setup",
                "No folder selected. The application cannot start without it.\n\n"
                "Retry?",
            )
            if not again:
                return False
            continue

        project_home = _resolve_project_home(Path(chosen_str))
        if project_home is None:
            # Пользователь сказал "нет" одному из подтверждений — пусть выбирает заново
            continue

        try:
            _ensure_article_db_structure(project_home)
        except Exception as e:
            messagebox.showerror(
                "First-time setup",
                f"Failed to create folder structure under:\n{project_home}\n\n"
                f"{type(e).__name__}: {e}",
            )
            return False

        try:
            data = {
                "PROJECT_HOME_DIR": str(project_home),
                "default_language": "EN",
            }
            saved_path = save_settings(data)
        except Exception as e:
            messagebox.showerror(
                "First-time setup",
                f"Failed to save settings:\n{type(e).__name__}: {e}",
            )
            return False

        messagebox.showinfo(
            "Setup complete",
            f"Settings saved to:\n  {saved_path}\n\n"
            f"PROJECT_HOME_DIR:\n  {project_home}\n\n"
            f"Drop PDF files into:\n  {project_home / ARTICLE_DB_FOLDER_NAME / NEW_FOLDER_NAME}",
        )
        return True


def main() -> None:
    root = tk.Tk()
    root.title("Brain Worm")

    # First-run check: if no settings.json found anywhere, ask the user.
    if find_settings_path() is None:
        if not _run_first_run_wizard(root):
            root.destroy()
            return

    try:
        MainWindow(root)
    except Exception as e:
        messagebox.showerror("Startup error", f"{type(e).__name__}: {e}")
        root.destroy()
        return

    root.mainloop()


if __name__ == "__main__":
    main()
