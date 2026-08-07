"""Byte-oriented lexical scanner for PDF syntax."""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum, auto
from typing import Iterator, Optional

class PDFLexError(ValueError):
    """Raised when PDF bytes cannot be tokenized."""

class TokenType(Enum):
    EOF=auto(); NUMBER=auto(); NAME=auto(); LITERAL_STRING=auto(); HEX_STRING=auto()
    TRUE=auto(); FALSE=auto(); NULL=auto(); ARRAY_START=auto(); ARRAY_END=auto()
    DICT_START=auto(); DICT_END=auto(); KEYWORD=auto(); COMMENT=auto()

@dataclass(frozen=True)
class Token:
    type: TokenType
    value: object=None
    start: int=0
    end: int=0
    def __repr__(self):
        return f"Token({self.type.name}, {self.value!r}, {self.start}:{self.end})"

class ByteReader:
    def __init__(self,data:bytes|bytearray|memoryview):
        self.data=bytes(data); self.pos=0
    def __len__(self): return len(self.data)
    def eof(self): return self.pos>=len(self.data)
    def tell(self): return self.pos
    def seek(self,position:int):
        if not 0<=position<=len(self.data): raise ValueError("position outside input")
        self.pos=position
    def peek(self,offset:int=0)->Optional[int]:
        i=self.pos+offset
        return None if i<0 or i>=len(self.data) else self.data[i]
    def read(self,count:int=1)->bytes:
        if count<0: raise ValueError("count must be non-negative")
        end=min(self.pos+count,len(self.data)); out=self.data[self.pos:end]; self.pos=end; return out
    def read_byte(self)->Optional[int]:
        c=self.peek()
        if c is not None: self.pos+=1
        return c

_WHITESPACE=frozenset((0,9,10,12,13,32))
_DELIMITERS=frozenset((40,41,60,62,91,93,123,125,47,37))
_HEX=frozenset(b"0123456789abcdefABCDEF")

def is_whitespace(byte:Optional[int])->bool: return byte in _WHITESPACE if byte is not None else False
def is_delimiter(byte:Optional[int])->bool: return byte in _DELIMITERS if byte is not None else False
def _regular(byte:Optional[int])->bool: return byte is not None and not is_whitespace(byte) and not is_delimiter(byte)
def _number_start(r:ByteReader)->bool:
    c=r.peek()
    if c is None:return False
    if 48<=c<=57 or c==46:return True
    return c in (43,45) and (r.peek(1) is not None and (48<=r.peek(1)<=57 or r.peek(1)==46))

class PDFLexer:
    def __init__(self,data:bytes|bytearray|memoryview): self.reader=ByteReader(data)
    @property
    def position(self): return self.reader.tell()
    def __iter__(self)->Iterator[Token]:
        while True:
            t=self.next_token(); yield t
            if t.type is TokenType.EOF:return
    def tokenize(self): return list(self)
    def skip_whitespace(self):
        while is_whitespace(self.reader.peek()): self.reader.read_byte()
    def next_token(self)->Token:
        self.skip_whitespace(); s=self.reader.tell(); c=self.reader.peek()
        if c is None:return Token(TokenType.EOF,None,s,s)
        if c==37:return self._comment()
        if c==47:return self._name()
        if c==40:return self._literal()
        if c==60:
            if self.reader.peek(1)==60:
                self.reader.read(2); return Token(TokenType.DICT_START,None,s,self.reader.tell())
            return self._hex_string()
        if c==62:
            if self.reader.peek(1)==62:
                self.reader.read(2); return Token(TokenType.DICT_END,None,s,self.reader.tell())
            raise PDFLexError(f"unexpected '>' at byte {s}")
        if c==91:
            self.reader.read_byte(); return Token(TokenType.ARRAY_START,None,s,self.reader.tell())
        if c==93:
            self.reader.read_byte(); return Token(TokenType.ARRAY_END,None,s,self.reader.tell())
        if _number_start(self.reader): return self._number()
        if _regular(c): return self._word()
        raise PDFLexError(f"unexpected byte 0x{c:02x} at byte {s}")
    def _comment(self):
        s=self.reader.tell(); self.reader.read_byte(); out=bytearray()
        while (c:=self.reader.peek()) is not None and c not in (10,13): out.append(self.reader.read_byte())
        return Token(TokenType.COMMENT,bytes(out),s,self.reader.tell())
    def _number(self):
        s=self.reader.tell(); d=self.reader.data; i=s
        if d[i] in (43,45):i+=1
        before=0
        while i<len(d) and 48<=d[i]<=57:i+=1; before+=1
        after=0
        if i<len(d) and d[i]==46:
            i+=1
            while i<len(d) and 48<=d[i]<=57:i+=1; after+=1
        if not before and not after:raise PDFLexError(f"invalid number at byte {s}")
        if i<len(d) and _regular(d[i]):raise PDFLexError(f"invalid number at byte {s}")
        raw=d[s:i]; self.reader.seek(i)
        try:v=float(raw) if b'.' in raw else int(raw)
        except ValueError as e:raise PDFLexError(f"invalid number at byte {s}") from e
        return Token(TokenType.NUMBER,v,s,i)
    def _name(self):
        s=self.reader.tell(); self.reader.read_byte(); out=bytearray()
        while _regular(self.reader.peek()):
            c=self.reader.read_byte()
            if c==35:
                a,b=self.reader.read_byte(),self.reader.read_byte()
                if a not in _HEX or b not in _HEX:raise PDFLexError(f"invalid name escape at byte {self.reader.tell()-2}")
                out.append(int(bytes((a,b)),16))
            else:out.append(c)
        return Token(TokenType.NAME,bytes(out),s,self.reader.tell())
    def _word(self):
        s=self.reader.tell(); out=bytearray()
        while _regular(self.reader.peek()):out.append(self.reader.read_byte())
        v=bytes(out); kinds={b'true':(TokenType.TRUE,True),b'false':(TokenType.FALSE,False),b'null':(TokenType.NULL,None)}
        typ,val=kinds.get(v,(TokenType.KEYWORD,v)); return Token(typ,val,s,self.reader.tell())
    def _literal(self):
        s=self.reader.tell(); self.reader.read_byte(); out=bytearray(); depth=1
        simple={110:10,114:13,116:9,98:8,102:12,40:40,41:41,92:92}
        while depth:
            c=self.reader.read_byte()
            if c is None:raise PDFLexError(f"unterminated literal string at byte {s}")
            if c==40:depth+=1; out.append(c); continue
            if c==41:
                depth-=1
                if depth:out.append(c)
                continue
            if c!=92:out.append(c); continue
            e=self.reader.read_byte()
            if e is None:raise PDFLexError(f"unterminated escape at byte {self.reader.tell()-1}")
            if e in simple:out.append(simple[e]); continue
            if e==13:
                if self.reader.peek()==10:self.reader.read_byte()
                continue
            if e==10:continue
            if 48<=e<=55:
                digs=bytearray((e,))
                for _ in range(2):
                    n=self.reader.peek()
                    if n is None or not 48<=n<=55:break
                    digs.append(self.reader.read_byte())
                out.append(int(bytes(digs),8)&255); continue
            out.append(e)
        return Token(TokenType.LITERAL_STRING,bytes(out),s,self.reader.tell())
    def _hex_string(self):
        s=self.reader.tell(); self.reader.read_byte(); digs=bytearray()
        while True:
            c=self.reader.peek()
            if c is None:raise PDFLexError(f"unterminated hexadecimal string at byte {s}")
            if c==62:self.reader.read_byte();break
            if is_whitespace(c):self.reader.read_byte();continue
            if c not in _HEX:raise PDFLexError(f"invalid hexadecimal digit at byte {self.reader.tell()}")
            digs.append(self.reader.read_byte())
        if len(digs)%2:digs.append(48)
        return Token(TokenType.HEX_STRING,bytes.fromhex(digs.decode('ascii')),s,self.reader.tell())

def lex(data:bytes|bytearray|memoryview)->Iterator[Token]: return iter(PDFLexer(data))

__all__=["ByteReader","PDFLexError","PDFLexer","Token","TokenType","is_delimiter","is_whitespace","lex"]
