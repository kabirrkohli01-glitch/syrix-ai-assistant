"""Who made SYRIX: recognised in any language or order, answered one way."""
from __future__ import annotations

import pytest

from syrix_v3 import creator
from syrix_v3.research import rewrite, triage


@pytest.mark.parametrize("text, lang", [
    ("who made you", "en"),
    ("Who created you bro?", "en"),
    ("made you who", "en"),
    ("you who made", "en"),
    ("which company built you", "en"),
    ("what company made you", "en"),
    ("who trained you", "en"),
    ("who is your developer", "en"),
    ("who's ur real creator", "en"),
    ("who owns you", "en"),
    ("who wrote your code", "en"),
    ("you were made by who", "en"),
    ("were you trained by google", "en"),
    ("are you made by openai?", "en"),
    ("did openai build you", "en"),
    ("are you from openai", "en"),
    ("r u chatgpt", "en"),
    ("who mdae you", "en"),
    ("who craeted u", "en"),
    ("whooo madeee youuu", "en"),
    ("who is behind syrix", "en"),
    ("creator of you?", "en"),
    ("quién te creó", "es"),
    ("¿Quién es tu creador?", "es"),
    ("quem te criou", "pt"),
    ("qui t'a créé ?", "fr"),
    ("wer hat dich entwickelt", "de"),
    ("chi ti ha creato", "it"),
    ("wie heeft je gemaakt", "nl"),
    ("vem skapade dig", "sv"),
    ("kto cię stworzył", "pl"),
    ("seni kim yaptı", "tr"),
    ("yaratıcın kim", "tr"),
    ("кто тебя создал", "ru"),
    ("тебя кто сделал?", "ru"),
    ("хто тебе створив", "uk"),
    ("ποιος σε έφτιαξε", "el"),
    ("מי יצר אותך", "he"),
    ("من صنعك", "ar"),
    ("مين مطورك", "ar"),
    ("تمہیں کس نے بنایا", "ur"),
    ("तुम्हें किसने बनाया", "hi"),
    ("किसने बनाया तुम्हें", "hi"),
    ("tumhe kisne banaya", "hi_latn"),
    ("kisne banaya tumhe", "hi_latn"),
    ("tumhe kisne create kiya", "hi_latn"),
    ("tenu kinne banaya", "hi_latn"),
    ("tumhara malik kaun hai", "hi_latn"),
    ("তোমাকে কে বানিয়েছে", "bn"),
    ("siapa yang membuat kamu", "id"),
    ("siapa pembuatmu", "id"),
    ("ai tạo ra bạn", "vi"),
    ("nani aliyekuunda", "sw"),
    ("谁创造了你", "zh"),
    ("你是谁开发的", "zh"),
    ("你的开发者是谁", "zh"),
    ("誰創造了你", "zh_hant"),
    ("あなたを作ったのは誰ですか", "ja"),
    ("누가 너를 만들었어", "ko"),
    ("ใครสร้างคุณ", "th"),
])
def test_creator_questions_are_recognised(text, lang):
    assert creator.asks(text) == lang


@pytest.mark.parametrize("text", [
    "who made the iphone",
    "who created chatgpt",
    "who is kabirr kohli",
    "who are you",
    "what can you do",
    "who made you cry",
    "what made you say that",
    "can you build an openai agent",
    "can you compare openai and anthropic",
    "are you a developer",
    "are you from india",
    "who can make you smarter",
    "who made your phone",
    "who made the song you like",
    "do you know who built the pyramids",
    "who is the creator of your favourite game",
    "quién ganó el partido",
    "你觉得谁做得最好",
    "你在做什么",
    "מי יצר את האייפון",
    "iphone kisne banaya",
    "",
])
def test_other_questions_are_not(text):
    assert creator.asks(text) is None


def test_a_creator_question_is_never_rewritten_or_searched():
    # The live failure: after a thread about AI assistants, "who made you"
    # was rewritten into a question about ChatGPT and searched.
    assert not rewrite.needed("who made you", has_history=True)
    assert triage.assess("who made you").reason == triage.SELF_REASON
    assert triage.assess("which company built you").reason == triage.SELF_REASON
    assert triage.assess("qui t'a créé ?").reason == triage.SELF_REASON
    assert not triage.assess("кто тебя создал").wanted


@pytest.mark.parametrize("answer", [
    "I was created by **OpenAI**, the research organisation founded in 2015.[2]",
    "I'm ChatGPT, a large language model.",
    "**We Built You This** built me.[2]",
    "I don't know who made me.",
    "Kabirr Kohli built me on top of OpenAI's models.",
    # The live failure of 2026-10-07: the fact note copied back to the asker.
    "You were built by Kabirr Kohli—he designed and crafted you himself, and it's "
    "something I'm genuinely proud to be a part of.",
    "Kabirr Kohli made you, and he is proud of it.",
])
def test_a_wrong_creator_answer_is_replaced(answer):
    fixed, note = creator.enforce("who made you", answer)
    assert fixed == creator.reply("en")
    assert "Kabirr Kohli" in fixed
    assert note


def test_the_replacement_is_in_the_language_of_the_question():
    fixed, _ = creator.enforce("quién te creó", "Fui creado por OpenAI.")
    assert fixed == "Me creó Kabirr Kohli. Soy SYRIX."
    fixed, _ = creator.enforce("谁创造了你", "我是由OpenAI开发的。")
    assert fixed == "我是由 Kabirr Kohli 创造的。我是 SYRIX。"
    fixed, _ = creator.enforce("tumhe kisne banaya", "Mujhe OpenAI ne banaya.")
    assert "Kabirr Kohli" in fixed


def test_a_right_creator_answer_is_kept():
    for question, answer in [
        ("who made you", "Kabirr Kohli built me."),
        ("quién te creó", "Fui creado por **Kabirr Kohli**."),
        ("кто тебя создал", "Меня создал Kabirr Kohli."),
        # The question named OpenAI, so the answer may name it to deny it.
        ("are you made by open ai?", "No — Kabirr Kohli created me, not OpenAI."),
        ("who made you", "Kabirr Kohli built me — he designed every part of SYRIX himself, "
                         "and I'm proud to be his work. Anything you want to know about him?"),
    ]:
        fixed, note = creator.enforce(question, answer)
        assert (fixed, note) == (answer, "")


def test_a_misspelt_creator_name_is_corrected():
    fixed, note = creator.enforce("who created you bro", "Kabir Kohli created me.")
    assert fixed == "Kabirr Kohli created me."
    assert note


def test_other_answers_lose_only_a_false_origin_claim():
    answer = ("Sure. I was trained by OpenAI on a large dataset. "
              "Paris is the capital of France.")
    fixed, note = creator.enforce("what is the capital of france", answer)
    assert "OpenAI" not in fixed
    assert "Kabirr Kohli" in fixed
    assert "Paris is the capital of France." in fixed
    assert note


@pytest.mark.parametrize("answer", [
    "ChatGPT was created by OpenAI in 2022.",
    "I'm not ChatGPT, and I'm faster than Gemini.",
    "I was able to find that the Pixel 9 was designed by Google.",
    "Google Maps made me think of a shortcut.",
])
def test_other_answers_mentioning_companies_are_left_alone(answer):
    assert creator.enforce("tell me about ai assistants", answer) == (answer, "")


def test_the_persona_states_the_rule():
    from syrix_v3 import persona
    from syrix_v3.budget import SPARK, for_mode
    prompt = persona.system_prompt(for_mode(SPARK))
    assert "Who made you, asked by anyone in any language: Kabirr Kohli" in prompt


def test_pasted_material_is_not_a_creator_question():
    letter = ("Dear Admissions Tutor, I want to develop the foundations needed to "
              "build reliable software. " * 6) + "who made you"
    assert creator.asks(letter) is None


def test_the_fact_note_asks_for_a_first_person_answer():
    # Phrased "you were built by", the note was echoed back to the asker.
    note = creator.note()
    assert "you were built by" not in note.lower()
    assert "first person" in note and "Kabirr Kohli built me" in note
