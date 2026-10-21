from birthdaybot.ui import Button, Heading, Para, Screen, Table, render_classic, render_rich


def test_rich_render_escapes_and_buttons():
    s = Screen().add(Heading("<Привет>"), Para("<b>ok</b>"), Table(["A"], [["x & y"]]))
    s.row(*[Button(str(i), f"d{i}") for i in range(10)])   # 10 кнопок → 2 ряда (макс. 8)
    html = render_rich(s)
    assert "<h2>&lt;Привет&gt;</h2>" in html
    assert "<td>x &amp; y</td>" in html
    assert html.count("<tg-button-row") == 2


def test_classic_render_table_as_pre():
    s = Screen().add(Table(["Имя", "Цена"], [["Иван", "100"]], align=["left", "right"]))
    s.row(Button("Ок", "ok"))
    text, markup = render_classic(s)
    assert text.startswith("<pre>") and "Иван" in text
    assert markup == {"inline_keyboard": [[{"text": "Ок", "callback_data": "ok"}]]}


def test_callback_data_limit():
    import pytest
    with pytest.raises(ValueError):
        Button("x", "a" * 65)
