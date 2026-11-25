"""Small HTML-backed test adapter, not a browser or Microsoft JS emulator.

Only the selector subset used by AuthFlow is implemented. Actual locator and
visibility semantics are additionally covered by opt-in Playwright tests.
"""

import re
from html.parser import HTMLParser
from pathlib import Path

from provadis_m1.auth_flow import origin

FIXTURES = Path(__file__).parent / "fixtures" / "auth"


class Node:
    def __init__(self, tag, attrs):
        self.tag, self.attrs, self.text = tag, dict(attrs), ""


class Document(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.nodes, self.stack = [], []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        node.form = next((n for n in reversed(self.stack) if n.tag == "form"), None)
        self.nodes.append(node)
        if tag not in {"input", "meta", "img", "br", "link"}:
            self.stack.append(node)

    def handle_data(self, text):
        for node in self.stack:
            node.text += text

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break


class Locator:
    def __init__(self, page, nodes):
        self.page, self.nodes = page, nodes

    def filter(self, visible=None, has_text=None):
        nodes = self.nodes
        if visible:
            nodes = [n for n in nodes if "display:none" not in n.attrs.get("style", "").replace(" ", "")]
        if has_text:
            nodes = [n for n in nodes if has_text in n.text]
        return Locator(self.page, nodes)

    def count(self):
        return len(self.nodes)

    def inner_text(self, **kwargs):
        return self.nodes[0].text

    def element_handle(self, **kwargs):
        return self if self.nodes else None

    def evaluate(self, expression, expected):
        from provadis_m1.credential_step import PREPARE, VERIFY_SUBMIT
        if expression in (PREPARE, VERIFY_SUBMIT):
            if origin(self.page.url) != expected["host"]:
                return {"status": "origin_changed"}
            node = self.nodes[0]
            if node not in self.page.document.nodes:
                return {"status": "detached"}
            record = self.page.records.get(expected["token"])
            if record and record["dispatched"]:
                return {"status": "dispatched"}
            fields = self.page.locator(expected["marker"]).nodes
            if expression == PREPARE:
                if not fields or fields != [node] or node.form is None:
                    return {"status": "form_changed"}
                if record is None and not expected["initial"]:
                    return {"status": "evidence_lost"}
                action = node.form.attrs["action"]
                if record and record["action"] != action:
                    return {"status": "form_changed"}
                self.page.records[expected["token"]] = {
                    "dispatched": False, "action": action, "form": node.form}
                return {"status": "ready", "action": action}
            if record is None:
                return {"status": "evidence_lost"}
            if not fields or node.form is not fields[0].form or node.form is not record["form"]:
                return {"status": "form_changed"}
            return {"status": "ready"}
        if origin(self.page.url) != expected["host"]:
            return "origin_changed"
        if expected["marker"] and self.page.locator(expected["marker"]).count() == 0:
            return "form_changed"
        return "ready"

    def fill(self, value, **kwargs):
        self.page.fills[self.nodes[0].attrs["name"]] = value

    def click(self, **kwargs):
        node = self.nodes[0]
        if node not in self.page.document.nodes:
            raise RuntimeError("Element is not attached to the DOM")
        for record in self.page.records.values():
            if node.attrs.get("id") == "idSIButton9" and node.form is record["form"]:
                record["dispatched"] = True
                if hasattr(self.page, "on_credential_request"):
                    self.page.on_credential_request(record["action"])
        self.page.clicks.append(node)
        if hasattr(self.page, "on_click"):
            self.page.on_click()

    def set_checked(self, value, **kwargs):
        self.page.checked = value

    def dispose(self):
        pass


class Page:
    def __init__(self, fixture, url):
        self.url, self.fills, self.clicks, self.checked = url, {}, [], False
        self.main_frame = object()
        self.load(fixture)

    def load(self, fixture):
        self.records = {}
        self.document = Document((FIXTURES / fixture).read_text() if fixture else "")

    def evaluate(self, expression, token):
        from provadis_m1.credential_step import READ_EVIDENCE
        assert expression == READ_EVIDENCE
        record = self.records.get(token)
        return {"dispatched": record["dispatched"]} if record else None

    def locator(self, css):
        nodes = self.document.nodes
        tag = re.match(r"^[a-z]+", css)
        if tag:
            nodes = [n for n in nodes if n.tag == tag[0]]
        ident = re.search(r"#([\w-]+)", css)
        if ident:
            nodes = [n for n in nodes if n.attrs.get("id") == ident[1]]
        if css.startswith("."):
            nodes = [n for n in nodes if css[1:] in n.attrs.get("class", "").split()]
        for key, value in re.findall(r'\[([\w-]+)="([^"]+)"\]', css):
            nodes = [n for n in nodes if n.attrs.get(key) == value]
        excluded = re.search(r":not\(\.([\w-]+)\)", css)
        if excluded:
            nodes = [n for n in nodes if excluded[1] not in n.attrs.get("class", "").split()]
        return Locator(self, nodes)

    def get_by_role(self, role, *, name, exact=True, level=None):
        def matches(n):
            actual = n.attrs.get("role")
            if actual is None:
                actual = {"button": "button", "h2": "heading"}.get(n.tag)
            label = n.attrs.get("aria-label", " ".join(n.text.split()))
            return (actual == role and label == name
                    and (level is None or n.tag == f"h{level}"))
        return Locator(self, [n for n in self.document.nodes if matches(n)])
