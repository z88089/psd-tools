"""
Descriptor data structure.

Descriptors are basic data structure used throughout PSD files.
"""
from __future__ import absolute_import, unicode_literals
import attr
import io
import logging
from collections import OrderedDict

from psd_tools2.decoder.base import (
    BaseElement, ValueElement, NumericElement, IntegerElement, ListElement,
    DictElement
)
from psd_tools2.constants import OSType, UnitFloatType, DescriptorClassID
from psd_tools2.validators import in_
from psd_tools2.utils import (
    read_fmt, write_fmt, read_unicode_string, write_unicode_string,
    write_bytes, read_length_block, write_length_block, write_padding,
    new_registry,
)

logger = logging.getLogger(__name__)


TYPES, register = new_registry(attribute='ostype')


def read_length_and_key(fp):
    """
    Helper to write descriptor classID and key.
    """
    length = read_fmt('I', fp)[0]
    key = fp.read(length or 4)
    if length == 0:
        try:
            return DescriptorClassID(key)
        except ValueError:
            logger.warning('Unknown classID: %r' % (key))

    return key  # Fallback.


def write_length_and_key(fp, value):
    """
    Helper to write descriptor classID and key.
    """
    if value in DescriptorClassID:
        length = (len(value.value) != 4) * len(value.value)
        written = write_fmt(fp, 'I', length)
        written += write_bytes(fp, value.value)
    else:
        written = write_fmt(fp, 'I', len(value))
        written += write_bytes(fp, value)
    return written


class _DescriptorMixin(DictElement):
    @classmethod
    def _read_body(cls, fp):
        name = read_unicode_string(fp, padding=1)
        classID = read_length_and_key(fp)
        items = []
        count = read_fmt('I', fp)[0]
        for _ in range(count):
            key = read_length_and_key(fp)
            ostype = OSType(fp.read(4))
            decoder = TYPES.get(ostype)
            if not decoder:
                raise ValueError('Unknown descriptor type %r' % ostype)

            value = decoder.read(fp)
            if value is None:
                warnings.warn("%r (%r) is None" % (key, ostype))
            items.append((key, value))

        return dict(name=name, classID=classID, items=items)

    def _write_body(self, fp):
        written = write_unicode_string(fp, self.name, padding=1)
        written += write_length_and_key(fp, self.classID)
        written += write_fmt(fp, 'I', len(self))
        for key in self:
            written += write_length_and_key(fp, key)
            written += write_bytes(fp, self[key].ostype.value)
            written += self[key].write(fp)
        return written

    def __getitem__(self, key):
        key = key.encode('ascii') if isinstance(key, str) else key
        return self._items[key]

    def __setitem__(self, key, value):
        key = key.encode('ascii') if isinstance(key, str) else key
        self._items[key] = value

    def _repr_pretty_(self, p, cycle):
        if cycle:
            return "{name}{{...}".format(name=self.__class__.__name__)

        prefix = '{cls}({name}){{'.format(
            cls=self.__class__.__name__,
            name=getattr(self.classID, 'name', self.classID),
        )
        with p.group(2, prefix, '}'):
            p.breakable('')
            for idx, key in enumerate(self):
                if idx:
                    p.text(',')
                    p.breakable()
                value = self[key]
                if hasattr(key, 'name'):
                    p.text(key.name)
                else:
                    p.pretty(key)
                p.text(': ')
                if isinstance(value, bytes):
                    p.text(trimmed_repr(value))
                else:
                    p.pretty(value)
            p.breakable('')


@register(OSType.DESCRIPTOR)
@attr.s
class Descriptor(_DescriptorMixin):
    """
    Dict-like descriptor structure.

    Example::

        for key in descriptor:
            print(descriptor[key])

    .. py:attribute:: name
    .. py:attribute:: classID
    """
    name = attr.ib(default='', type=str)
    classID = attr.ib(default=DescriptorClassID.NULL)

    @classmethod
    def read(cls, fp):
        """Read the element from a file-like object.

        :param fp: file-like object
        """
        return cls(**cls._read_body(fp))

    def write(self, fp):
        """Write the element to a file-like object.

        :param fp: file-like object
        """
        return self._write_body(fp)


@register(OSType.OBJECT_ARRAY)
@attr.s(repr=False)
class ObjectArray(_DescriptorMixin):
    """
    Object array structure almost equivalent to
    :py:class:`~psd_tools2.decoder.descriptor.Descriptor`.

    .. py:attribute:: items_count
    .. py:attribute:: name
    .. py:attribute:: classID
    """
    items_count = attr.ib(default=0, type=int)
    name = attr.ib(default='', type=str)
    classID = attr.ib(default=DescriptorClassID.NULL)

    @classmethod
    def read(cls, fp):
        """Read the element from a file-like object.

        :param fp: file-like object
        """
        items_count = read_fmt('I', fp)[0]
        return cls(items_count=items_count, **cls._read_body(fp))

    def write(self, fp):
        """Write the element to a file-like object.

        :param fp: file-like object
        """
        written = write_fmt(fp, 'I', self.items_count)
        written += self._write_body(fp)
        return written


@register(OSType.LIST)
@attr.s(repr=False)
class List(ListElement):
    """
    List structure.

    .. py:attribute:: items
    """
    @classmethod
    def read(cls, fp):
        """Read the element from a file-like object.

        :param fp: file-like object
        """
        items = []
        count = read_fmt('I', fp)[0]
        for _ in range(count):
            key = OSType(fp.read(4))
            decoder = TYPES.get(key)
            if not decoder:
                raise ValueError('Unknown key %r' % key)
            value = decoder.read(fp)
            items.append(value)
        return cls(items)

    def write(self, fp):
        """Write the element to a file-like object.

        :param fp: file-like object
        """
        written = write_fmt(fp, 'I', len(self))
        for item in self:
            written += write_bytes(fp, item.ostype.value)
            written += item.write(fp)
        return written


@register(OSType.PROPERTY)
@attr.s
class Property(BaseElement):
    """
    Property structure.

    .. py:attribute:: name
    """
    name = attr.ib(default='', type=str)
    classID = attr.ib(default=b'\x00\x00\x00\x00', type=bytes)
    keyID = attr.ib(default=b'\x00\x00\x00\x00', type=bytes)

    @classmethod
    def read(cls, fp):
        """Read the element from a file-like object.

        :param fp: file-like object
        """
        name = read_unicode_string(fp)
        classID = read_length_and_key(fp)
        keyID = read_length_and_key(fp)
        return cls(name, classID, keyID)

    def write(self, fp):
        """Write the element to a file-like object.

        :param fp: file-like object
        """
        written = write_unicode_string(fp, self.name)
        written += write_length_and_key(fp, self.classID)
        written += write_length_and_key(fp, self.keyID)
        return written


@register(OSType.UNIT_FLOAT)
@attr.s
class UnitFloat(BaseElement):
    """
    Unit float structure.

    .. py:attribute:: unit
    .. py:attribute:: value
    """
    unit = attr.ib(default=UnitFloatType.NONE, converter=UnitFloatType,
                   validator=in_(UnitFloatType))
    value = attr.ib(default=0.0, type=float)

    @classmethod
    def read(cls, fp):
        """Read the element from a file-like object.

        :param fp: file-like object
        """
        return cls(*read_fmt('4sd', fp))

    def write(self, fp):
        """Write the element to a file-like object.

        :param fp: file-like object
        """
        return write_fmt(fp, '4sd', self.unit.value, self.value)

    def _repr_pretty_(self, p, cycle):
        if cycle:
            return self.__repr__()
        p.pretty(self.value)
        p.text(' ')
        p.text(self.unit.name)


@register(OSType.UNIT_FLOATS)
@attr.s
class UnitFloats(BaseElement):
    """
    Unit floats structure.

    .. py:attribute:: unit
    .. py:attribute:: values
    """
    unit = attr.ib(default=UnitFloatType.NONE, converter=UnitFloatType,
                   validator=in_(UnitFloatType))
    values = attr.ib(factory=list)

    @classmethod
    def read(cls, fp):
        """Read the element from a file-like object.

        :param fp: file-like object
        """
        unit, count = read_fmt('4sI', fp)
        values = list(read_fmt('%dd' % count, fp))
        return cls(unit, values)

    def write(self, fp):
        """Write the element to a file-like object.

        :param fp: file-like object
        """
        return write_fmt(fp, '4sI%dd' % len(self.values), self.unit.value,
                         len(self.values), *self.values)

    def __iter__(self):
        for value in self.values:
            yield value

    def __getitem__(self, index):
        return self.values[index]

    def __len__(self):
        return len(self.values)


@register(OSType.DOUBLE)
@attr.s(repr=False)
class Double(NumericElement):
    """
    Double structure.

    .. py:attribute:: value
    """
    value = attr.ib(default=0.0, type=float)

    @classmethod
    def read(cls, fp):
        """Read the element from a file-like object.

        :param fp: file-like object
        """
        return cls(*read_fmt('d', fp))

    def write(self, fp):
        """Write the element to a file-like object.

        :param fp: file-like object
        """
        return write_fmt(fp, 'd', self.value)


@attr.s
class Class(BaseElement):
    """
    Class structure.

    .. py:attribute:: name
    .. py:attribute:: classID
    """
    name = attr.ib(default='', type=str)
    classID = attr.ib(default=b'\x00\x00\x00\x00', type=bytes)

    @classmethod
    def read(cls, fp):
        """Read the element from a file-like object.

        :param fp: file-like object
        """
        name = read_unicode_string(fp)
        classID = read_length_and_key(fp)
        return cls(name, classID)

    def write(self, fp):
        """Write the element to a file-like object.

        :param fp: file-like object
        """
        written = write_unicode_string(fp, self.name)
        written += write_length_and_key(fp, self.classID)
        return written


@register(OSType.STRING)
@attr.s(repr=False)
class String(ValueElement):
    """
    String structure.

    .. py:attribute:: value
    """
    value = attr.ib(default='', type=str)

    @classmethod
    def read(cls, fp):
        """Read the element from a file-like object.

        :param fp: file-like object
        """
        return cls(read_unicode_string(fp, padding=1))

    def write(self, fp):
        """Write the element to a file-like object.

        :param fp: file-like object
        """
        return write_unicode_string(fp, self.value, padding=1)


@register(OSType.ENUMERATED_REFERENCE)
@attr.s
class EnumeratedReference(BaseElement):
    """
    Enumerated reference structure.

    .. py:attribute:: value
    """
    name = attr.ib(default='', type=str)
    classID = attr.ib(default=b'\x00\x00\x00\x00', type=bytes)
    typeID = attr.ib(default=b'\x00\x00\x00\x00', type=bytes)
    enum = attr.ib(default=b'\x00\x00\x00\x00', type=bytes)

    @classmethod
    def read(cls, fp):
        """Read the element from a file-like object.

        :param fp: file-like object
        """
        name = read_unicode_string(fp)
        classID = read_length_and_key(fp)
        typeID = read_length_and_key(fp)
        enum = read_length_and_key(fp)
        return cls(name, classID, typeID, enum)

    def write(self, fp):
        """Write the element to a file-like object.

        :param fp: file-like object
        """
        written = write_unicode_string(fp, self.name)
        written += write_length_and_key(fp, self.classID)
        written += write_length_and_key(fp, self.typeID)
        written += write_length_and_key(fp, self.enum)
        return written


@register(OSType.OFFSET)
@attr.s
class Offset(BaseElement):
    """
    Offset structure.

    .. py:attribute:: value
    """
    name = attr.ib(default='', type=str)
    classID = attr.ib(default=b'\x00\x00\x00\x00', type=bytes)
    value = attr.ib(default=0)

    @classmethod
    def read(cls, fp):
        """Read the element from a file-like object.

        :param fp: file-like object
        """
        name = read_unicode_string(fp)
        classID = read_length_and_key(fp)
        offset = read_fmt('I', fp)[0]
        return cls(name, classID, offset)

    def write(self, fp):
        """Write the element to a file-like object.

        :param fp: file-like object
        """
        written = write_unicode_string(fp, self.name)
        written += write_length_and_key(fp, self.classID)
        written += write_fmt(fp, 'I', self.value)
        return written


@register(OSType.BOOLEAN)
@attr.s(repr=False)
class Bool(IntegerElement):
    """
    Bool structure.

    .. py:attribute:: value
    """
    value = attr.ib(default=False, type=bool)

    @classmethod
    def read(cls, fp):
        """Read the element from a file-like object.

        :param fp: file-like object
        """
        return cls(read_fmt('?', fp)[0])

    def write(self, fp):
        """Write the element to a file-like object.

        :param fp: file-like object
        """
        return write_fmt(fp, '?', self.value)


@register(OSType.LARGE_INTEGER)
@attr.s(repr=False)
class LargeInteger(IntegerElement):
    """
    LargeInteger structure.

    .. py:attribute:: value
    """
    value = attr.ib(default=0, type=int)

    @classmethod
    def read(cls, fp):
        """Read the element from a file-like object.

        :param fp: file-like object
        """
        return cls(read_fmt('q', fp)[0])

    def write(self, fp):
        """Write the element to a file-like object.

        :param fp: file-like object
        """
        return write_fmt(fp, 'q', self.value)


@register(OSType.INTEGER)
@attr.s(repr=False)
class Integer(IntegerElement):
    """
    Integer structure.

    .. py:attribute:: value
    """
    value = attr.ib(default=0, type=int)

    @classmethod
    def read(cls, fp):
        """Read the element from a file-like object.

        :param fp: file-like object
        """
        return cls(read_fmt('i', fp)[0])

    def write(self, fp):
        """Write the element to a file-like object.

        :param fp: file-like object
        """
        return write_fmt(fp, 'i', self.value)


@register(OSType.ENUMERATED)
@attr.s
class Enum(BaseElement):
    """
    Enum structure.

    .. py:attribute:: value
    """
    typeID = attr.ib(default=b'\x00\x00\x00\x00', type=bytes)
    enum = attr.ib(default=b'\x00\x00\x00\x00', type=bytes)

    @classmethod
    def read(cls, fp):
        """Read the element from a file-like object.

        :param fp: file-like object
        """
        typeID = read_length_and_key(fp)
        enum = read_length_and_key(fp)
        return cls(typeID, enum)

    def write(self, fp):
        """Write the element to a file-like object.

        :param fp: file-like object
        """
        written = write_length_and_key(fp, self.typeID)
        written += write_length_and_key(fp, self.enum)
        return written

    def _repr_pretty_(self, p, cycle):
        if cycle:
            return self.__repr__()
        p.text('(')
        p.pretty(getattr(self.typeID, 'name', self.typeID))
        p.text(', ')
        p.pretty(getattr(self.enum, 'name', self.enum))
        p.text(')')


@register(OSType.RAW_DATA)
@attr.s
class RawData(BaseElement):
    """
    RawData structure.

    .. py:attribute:: value

        `bytes`
    """
    value = attr.ib(default=b'\x00\x00\x00\x00', type=bytes)

    @classmethod
    def read(cls, fp):
        """Read the element from a file-like object.

        :param fp: file-like object
        """
        return cls(read_length_block(fp))

    def write(self, fp):
        """Write the element to a file-like object.

        :param fp: file-like object
        """
        def writer(f):
            if hasattr(self.value, 'write'):
                return self.value.write(f)
            return write_bytes(f, self.value)
        return write_length_block(fp, writer)


@register(OSType.CLASS1)
class Class1(Class):
    """
    Class structure equivalent to
    :py:class:`~psd_tools2.decoder.descriptor.Class`.
    """
    pass


@register(OSType.CLASS2)
class Class2(Class):
    """
    Class structure equivalent to
    :py:class:`~psd_tools2.decoder.descriptor.Class`.
    """
    pass


@register(OSType.CLASS3)
class Class3(Class):
    """
    Class structure equivalent to
    :py:class:`~psd_tools2.decoder.descriptor.Class`.
    """
    pass


@register(OSType.REFERENCE)
class Reference(List):
    """
    Reference structure equivalent to
    :py:class:`~psd_tools2.decoder.descriptor.List`.
    """
    pass


@register(OSType.ALIAS)
class Alias(RawData):
    """
    Alias structure equivalent to
    :py:class:`~psd_tools2.decoder.descriptor.RawData`.

    .. py:attribute:: value
    """
    pass


@register(OSType.GLOBAL_OBJECT)
class GlobalObject(Descriptor):
    """
    Global object structure equivalent to
    :py:class:`~psd_tools2.decoder.descriptor.Descriptor`.
    """
    pass


@register(OSType.PATH)
class Path(RawData):
    """
    Undocumented path structure equivalent to
    :py:class:`~psd_tools2.decoder.descriptor.RawData`.
    """
    pass


@register(OSType.IDENTIFIER)
class Identifier(Integer):
    """
    Identifier equivalent to
    :py:class:`~psd_tools2.decoder.descriptor.Integer`.
    """
    pass


@register(OSType.INDEX)
class Index(Integer):
    """
    Index equivalent to :py:class:`~psd_tools2.decoder.descriptor.Integer`.
    """
    pass


@register(OSType.NAME)
class Name(String):
    """
    Name equivalent to :py:class:`~psd_tools2.decoder.descriptor.String`.
    """
    pass


@attr.s(repr=False)
class DescriptorBlock(Descriptor):
    """
    Dict-like Descriptor-based structure. See
    :py:class:`~psd_tools2.decoder.descriptor.Descriptor`.

    .. py:attribute:: version
    """
    version = attr.ib(default=1, type=int)

    @classmethod
    def read(cls, fp, **kwargs):
        version = read_fmt('I', fp)[0]
        return cls(version=version, **cls._read_body(fp))

    def write(self, fp, padding=4):
        written = write_fmt(fp, 'I', self.version)
        written += self._write_body(fp)
        written += write_padding(fp, written, padding)
        return written


@attr.s(repr=False)
class DescriptorBlock2(Descriptor):
    """
    Dict-like Descriptor-based structure. See
    :py:class:`~psd_tools2.decoder.descriptor.Descriptor`.

    .. py:attribute:: version
    .. py:attribute:: data_version
    """
    version = attr.ib(default=1, type=int)
    data_version = attr.ib(default=16, type=int, validator=in_((16,)))

    @classmethod
    def read(cls, fp, **kwargs):
        version, data_version = read_fmt('2I', fp)
        return cls(version=version, data_version=data_version,
                   **cls._read_body(fp))

    def write(self, fp, padding=4):
        written = write_fmt(fp, '2I', self.version, self.data_version)
        written += self._write_body(fp)
        written += write_padding(fp, written, padding)
        return written
