# -*- coding: utf-8 -*-

from __future__ import print_function, division, absolute_import
import collections
import logging
import os
import posixpath
import plistlib
import warnings

try:
    import xml.etree.cElementTree as ET
except ImportError:
    import xml.etree.ElementTree as ET

"""
    designSpaceDocument

    - read and write designspace files
"""

__all__ = [
    'DesignSpaceDocumentError', 'DesignSpaceDocument', 'SourceDescriptor',
    'InstanceDescriptor', 'AxisDescriptor', 'RuleDescriptor', 'BaseDocReader',
    'BaseDocWriter'
]


def to_plist(value):
    try:
        # Python 2
        string = plistlib.writePlistToString(value)
    except AttributeError:
        # Python 3
        string = plistlib.dumps(value).decode()
    return ET.fromstring(string)[0]


def from_plist(element):
    if element is None:
        return {}
    plist = ET.Element('plist')
    plist.append(element)
    string = ET.tostring(plist)
    try:
        # Python 2
        return plistlib.readPlistFromString(string)
    except AttributeError:
        # Python 3
        return plistlib.loads(string, fmt=plistlib.FMT_XML)


def posix(path):
    """Normalize paths using forward slash to work also on Windows."""
    new_path = posixpath.join(*path.split(os.path.sep))
    if path.startswith('/'):
        # The above transformation loses absolute paths
        new_path = '/' + new_path
    return new_path


def posixpath_property(private_name):
    def getter(self):
        # Normal getter
        return getattr(self, private_name)

    def setter(self, value):
        # The setter rewrites paths using forward slashes
        if value is not None:
            value = posix(value)
        setattr(self, private_name, value)

    return property(getter, setter)


class DesignSpaceDocumentError(Exception):
    def __init__(self, msg, obj=None):
        self.msg = msg
        self.obj = obj

    def __str__(self):
        return repr(self.msg) + repr(self.obj)


def _indent(elem, whitespace="    ", level=0):
    # taken from http://effbot.org/zone/element-lib.htm#prettyprint
    i = "\n" + level * whitespace
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + whitespace
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
        for elem in elem:
            _indent(elem, whitespace, level+1)
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = i


class SimpleDescriptor(object):
    """ Containers for a bunch of attributes"""
    def compare(self, other):
        # test if this object contains the same data as the other
        for attr in self._attrs:
            try:
                assert(getattr(self, attr) == getattr(other, attr))
            except AssertionError:
                print("failed attribute", attr, getattr(self, attr), "!=", getattr(other, attr))


class SourceDescriptor(SimpleDescriptor):
    """Simple container for data related to the source"""
    flavor = "source"
    _attrs = ['filename', 'path', 'name', 'layerName',
              'location', 'copyLib',
              'copyGroups', 'copyFeatures',
              'muteKerning', 'muteInfo',
              'mutedGlyphNames',
              'familyName', 'styleName']

    def __init__(self):
        self.filename = None
        """The original path as found in the document."""

        self.path = None
        """The absolute path, calculated from filename."""

        self.font = None
        """Any Python object. Optional. Points to a representation of this
        source font that is loaded in memory, as a Python object (e.g. a
        ``defcon.Font`` or a ``fontTools.ttFont.TTFont``).

        The default document reader will not fill-in this attribute, and the
        default writer will not use this attribute. It is up to the user of
        ``designspaceLib`` to either load the resource identified by
        ``filename`` and store it in this field, or write the contents of
        this field to the disk and make ```filename`` point to that.
        """

        self.name = None
        self.location = None
        self.layerName = None
        self.copyLib = False
        self.copyInfo = False
        self.copyGroups = False
        self.copyFeatures = False
        self.muteKerning = False
        self.muteInfo = False
        self.mutedGlyphNames = []
        self.familyName = None
        self.styleName = None

    path = posixpath_property("_path")
    filename = posixpath_property("_filename")


class RuleDescriptor(SimpleDescriptor):
    """<!-- optional: list of substitution rules -->
    <rules>
        <rule name="vertical.bars">
            <conditionset>
                <condition minimum="250.000000" maximum="750.000000" name="weight"/>
                <condition minimum="100" name="width"/>
                <condition minimum="10" maximum="40" name="optical"/>
            </conditionset>
            <sub name="cent" with="cent.alt"/>
            <sub name="dollar" with="dollar.alt"/>
        </rule>
    </rules>
    """
    _attrs = ['name', 'conditionSets', 'subs']   # what do we need here
    def __init__(self):
        self.name = None
        self.conditionSets = []  # list of list of dict(name='aaaa', minimum=0, maximum=1000)
        self.subs = []  # list of substitutions stored as tuples of glyphnames ("a", "a.alt")


def evaluateRule(rule, location):
    """ Return True if any of the rule's conditionsets matches the given location."""
    return any(evaluateConditions(c, location) for c in rule.conditionSets)


def evaluateConditions(conditions, location):
    """ Return True if all the conditions matches the given location.
        If a condition has no minimum, check for < maximum.
        If a condition has no maximum, check for > minimum.
    """
    for cd in conditions:
        value = location[cd['name']]
        if cd.get('minimum') is None:
            if value > cd['maximum']:
                return False
        elif cd.get('maximum') is None:
            if cd['minimum'] > value:
                return False
        elif not cd['minimum'] <= value <= cd['maximum']:
            return False
    return True


def processRules(rules, location, glyphNames):
    """ Apply these rules at this location to these glyphnames.minimum
        - rule order matters
    """
    newNames = []
    for rule in rules:
        if evaluateRule(rule, location):
            for name in glyphNames:
                swap = False
                for a, b in rule.subs:
                    if name == a:
                        swap = True
                        break
                if swap:
                    newNames.append(b)
                else:
                    newNames.append(name)
            glyphNames = newNames
            newNames = []
    return glyphNames


class InstanceDescriptor(SimpleDescriptor):
    """Simple container for data related to the instance"""
    flavor = "instance"
    _defaultLanguageCode = "en"
    _attrs = [  'path',
                'name',
                'location',
                'familyName',
                'styleName',
                'postScriptFontName',
                'styleMapFamilyName',
                'styleMapStyleName',
                'kerning',
                'info',
                'lib']

    def __init__(self):
        self.filename = None    # the original path as found in the document
        self.path = None        # the absolute path, calculated from filename
        self.name = None
        self.location = None
        self.familyName = None
        self.styleName = None
        self.postScriptFontName = None
        self.styleMapFamilyName = None
        self.styleMapStyleName = None
        self.localisedStyleName = {}
        self.localisedFamilyName = {}
        self.localisedStyleMapStyleName = {}
        self.localisedStyleMapFamilyName = {}
        self.glyphs = {}
        self.mutedGlyphNames = []
        self.kerning = True
        self.info = True

        self.lib = {}
        """Custom data associated with this instance."""

    path = posixpath_property("_path")
    filename = posixpath_property("_filename")

    def setStyleName(self, styleName, languageCode="en"):
        self.localisedStyleName[languageCode] = styleName
    def getStyleName(self, languageCode="en"):
        return self.localisedStyleName.get(languageCode)

    def setFamilyName(self, familyName, languageCode="en"):
        self.localisedFamilyName[languageCode] = familyName
    def getFamilyName(self, languageCode="en"):
        return self.localisedFamilyName.get(languageCode)

    def setStyleMapStyleName(self, styleMapStyleName, languageCode="en"):
        self.localisedStyleMapStyleName[languageCode] = styleMapStyleName
    def getStyleMapStyleName(self, languageCode="en"):
        return self.localisedStyleMapStyleName.get(languageCode)

    def setStyleMapFamilyName(self, styleMapFamilyName, languageCode="en"):
        self.localisedStyleMapFamilyName[languageCode] = styleMapFamilyName
    def getStyleMapFamilyName(self, languageCode="en"):
        return self.localisedStyleMapFamilyName.get(languageCode)

def tagForAxisName(name):
    # try to find or make a tag name for this axis name
    names = {
        'weight':   ('wght', dict(en = 'Weight')),
        'width':    ('wdth', dict(en = 'Width')),
        'optical':  ('opsz', dict(en = 'Optical Size')),
        'slant':    ('slnt', dict(en = 'Slant')),
        'italic':   ('ital', dict(en = 'Italic')),
    }
    if name.lower() in names:
        return names[name.lower()]
    if len(name) < 4:
        tag = name + "*"*(4-len(name))
    else:
        tag = name[:4]
    return tag, dict(en = name)


class AxisDescriptor(SimpleDescriptor):
    """ Simple container for the axis data
        Add more localisations?
    """
    flavor = "axis"
    _attrs = ['tag', 'name', 'maximum', 'minimum', 'default', 'map']

    def __init__(self):
        self.tag = None       # opentype tag for this axis
        self.name = None      # name of the axis used in locations
        self.labelNames = {}  # names for UI purposes, if this is not a standard axis,
        self.minimum = None
        self.maximum = None
        self.default = None
        self.hidden = False
        self.map = []

    def serialize(self):
        # output to a dict, used in testing
        d = dict(tag = self.tag,
                name = self.name,
                labelNames = self.labelNames,
                maximum = self.maximum,
                minimum = self.minimum,
                default = self.default,
                hidden = self.hidden,
                map = self.map,
            )
        return d


class BaseDocWriter(object):
    _whiteSpace = "    "
    ruleDescriptorClass = RuleDescriptor
    axisDescriptorClass = AxisDescriptor
    sourceDescriptorClass = SourceDescriptor
    instanceDescriptorClass = InstanceDescriptor

    @classmethod
    def getAxisDecriptor(cls):
        return cls.axisDescriptorClass()

    @classmethod
    def getSourceDescriptor(cls):
        return cls.sourceDescriptorClass()

    @classmethod
    def getInstanceDescriptor(cls):
        return cls.instanceDescriptorClass()

    @classmethod
    def getRuleDescriptor(cls):
        return cls.ruleDescriptorClass()

    def __init__(self, documentPath, documentObject):
        self.path = documentPath
        self.documentObject = documentObject
        self.documentVersion = "4.0"
        self.root = ET.Element("designspace")
        self.root.attrib['format'] = self.documentVersion
        self._axes = []     # for use by the writer only
        self._rules = []    # for use by the writer only

    def write(self, pretty=True):
        if self.documentObject.axes:
            self.root.append(ET.Element("axes"))
        for axisObject in self.documentObject.axes:
            self._addAxis(axisObject)

        if self.documentObject.rules:
            self.root.append(ET.Element("rules"))
        for ruleObject in self.documentObject.rules:
            self._addRule(ruleObject)

        if self.documentObject.sources:
            self.root.append(ET.Element("sources"))
        for sourceObject in self.documentObject.sources:
            self._addSource(sourceObject)

        if self.documentObject.instances:
            self.root.append(ET.Element("instances"))
        for instanceObject in self.documentObject.instances:
            self._addInstance(instanceObject)

        if self.documentObject.lib:
            self._addLib(self.documentObject.lib)

        if pretty:
            _indent(self.root, whitespace=self._whiteSpace)
        tree = ET.ElementTree(self.root)
        tree.write(self.path, encoding="utf-8", method='xml', xml_declaration=True)

    def _makeLocationElement(self, locationObject, name=None):
        """ Convert Location dict to a locationElement."""
        locElement = ET.Element("location")
        if name is not None:
            locElement.attrib['name'] = name
        validatedLocation = self.documentObject.newDefaultLocation()
        for axisName, axisValue in locationObject.items():
            if axisName in validatedLocation:
                # only accept values we know
                validatedLocation[axisName] = axisValue
        for dimensionName, dimensionValue in validatedLocation.items():
            dimElement = ET.Element('dimension')
            dimElement.attrib['name'] = dimensionName
            if type(dimensionValue) == tuple:
                dimElement.attrib['xvalue'] = self.intOrFloat(dimensionValue[0])
                dimElement.attrib['yvalue'] = self.intOrFloat(dimensionValue[1])
            else:
                dimElement.attrib['xvalue'] = self.intOrFloat(dimensionValue)
            locElement.append(dimElement)
        return locElement, validatedLocation

    def intOrFloat(self, num):
        if int(num) == num:
            return "%d" % num
        return "%f" % num

    def _addRule(self, ruleObject):
        # if none of the conditions have minimum or maximum values, do not add the rule.
        self._rules.append(ruleObject)
        ruleElement  = ET.Element('rule')
        if ruleObject.name is not None:
            ruleElement.attrib['name'] = ruleObject.name
        for conditions in ruleObject.conditionSets:
            conditionsetElement = ET.Element('conditionset')
            for cond in conditions:
                if cond.get('minimum') is None and cond.get('maximum') is None:
                    # neither is defined, don't add this condition
                    continue
                conditionElement = ET.Element('condition')
                conditionElement.attrib['name'] = cond.get('name')
                if cond.get('minimum') is not None:
                    conditionElement.attrib['minimum'] = self.intOrFloat(cond.get('minimum'))
                if cond.get('maximum') is not None:
                    conditionElement.attrib['maximum'] = self.intOrFloat(cond.get('maximum'))
                conditionsetElement.append(conditionElement)
            if len(conditionsetElement):
                ruleElement.append(conditionsetElement)
        for sub in ruleObject.subs:
            subElement = ET.Element('sub')
            subElement.attrib['name'] = sub[0]
            subElement.attrib['with'] = sub[1]
            ruleElement.append(subElement)
        if len(ruleElement):
            self.root.findall('.rules')[0].append(ruleElement)

    def _addAxis(self, axisObject):
        self._axes.append(axisObject)
        axisElement = ET.Element('axis')
        axisElement.attrib['tag'] = axisObject.tag
        axisElement.attrib['name'] = axisObject.name
        axisElement.attrib['minimum'] = self.intOrFloat(axisObject.minimum)
        axisElement.attrib['maximum'] = self.intOrFloat(axisObject.maximum)
        axisElement.attrib['default'] = self.intOrFloat(axisObject.default)
        if axisObject.hidden:
            axisElement.attrib['hidden'] = "1"
        for languageCode, labelName in sorted(axisObject.labelNames.items()):
            languageElement = ET.Element('labelname')
            languageElement.attrib[u'xml:lang'] = languageCode
            languageElement.text = labelName
            axisElement.append(languageElement)
        if axisObject.map:
            for inputValue, outputValue in axisObject.map:
                mapElement = ET.Element('map')
                mapElement.attrib['input'] = self.intOrFloat(inputValue)
                mapElement.attrib['output'] = self.intOrFloat(outputValue)
                axisElement.append(mapElement)
        self.root.findall('.axes')[0].append(axisElement)

    def _addInstance(self, instanceObject):
        instanceElement = ET.Element('instance')
        if instanceObject.name is not None:
            instanceElement.attrib['name'] = instanceObject.name
        if instanceObject.familyName is not None:
            instanceElement.attrib['familyname'] = instanceObject.familyName
        if instanceObject.styleName is not None:
            instanceElement.attrib['stylename'] = instanceObject.styleName
        # add localisations
        if instanceObject.localisedStyleName:
            languageCodes = list(instanceObject.localisedStyleName.keys())
            languageCodes.sort()
            for code in languageCodes:
                if code == "en": continue # already stored in the element attribute
                localisedStyleNameElement = ET.Element('stylename')
                localisedStyleNameElement.attrib["xml:lang"] = code
                localisedStyleNameElement.text = instanceObject.getStyleName(code)
                instanceElement.append(localisedStyleNameElement)
        if instanceObject.localisedFamilyName:
            languageCodes = list(instanceObject.localisedFamilyName.keys())
            languageCodes.sort()
            for code in languageCodes:
                if code == "en": continue # already stored in the element attribute
                localisedFamilyNameElement = ET.Element('familyname')
                localisedFamilyNameElement.attrib["xml:lang"] = code
                localisedFamilyNameElement.text = instanceObject.getFamilyName(code)
                instanceElement.append(localisedFamilyNameElement)
        if instanceObject.localisedStyleMapStyleName:
            languageCodes = list(instanceObject.localisedStyleMapStyleName.keys())
            languageCodes.sort()
            for code in languageCodes:
                if code == "en": continue
                localisedStyleMapStyleNameElement = ET.Element('stylemapstylename')
                localisedStyleMapStyleNameElement.attrib["xml:lang"] = code
                localisedStyleMapStyleNameElement.text = instanceObject.getStyleMapStyleName(code)
                instanceElement.append(localisedStyleMapStyleNameElement)
        if instanceObject.localisedStyleMapFamilyName:
            languageCodes = list(instanceObject.localisedStyleMapFamilyName.keys())
            languageCodes.sort()
            for code in languageCodes:
                if code == "en": continue
                localisedStyleMapFamilyNameElement = ET.Element('stylemapfamilyname')
                localisedStyleMapFamilyNameElement.attrib["xml:lang"] = code
                localisedStyleMapFamilyNameElement.text = instanceObject.getStyleMapFamilyName(code)
                instanceElement.append(localisedStyleMapFamilyNameElement)

        if instanceObject.location is not None:
            locationElement, instanceObject.location = self._makeLocationElement(instanceObject.location)
            instanceElement.append(locationElement)
        if instanceObject.filename is not None:
            instanceElement.attrib['filename'] = instanceObject.filename
        if instanceObject.postScriptFontName is not None:
            instanceElement.attrib['postscriptfontname'] = instanceObject.postScriptFontName
        if instanceObject.styleMapFamilyName is not None:
            instanceElement.attrib['stylemapfamilyname'] = instanceObject.styleMapFamilyName
        if instanceObject.styleMapStyleName is not None:
            instanceElement.attrib['stylemapstylename'] = instanceObject.styleMapStyleName
        if instanceObject.glyphs:
            if instanceElement.findall('.glyphs') == []:
                glyphsElement = ET.Element('glyphs')
                instanceElement.append(glyphsElement)
            glyphsElement = instanceElement.findall('.glyphs')[0]
            for glyphName, data in sorted(instanceObject.glyphs.items()):
                glyphElement = self._writeGlyphElement(instanceElement, instanceObject, glyphName, data)
                glyphsElement.append(glyphElement)
        if instanceObject.kerning:
            kerningElement = ET.Element('kerning')
            instanceElement.append(kerningElement)
        if instanceObject.info:
            infoElement = ET.Element('info')
            instanceElement.append(infoElement)
        if instanceObject.lib:
            libElement = ET.Element('lib')
            libElement.append(to_plist(instanceObject.lib))
            instanceElement.append(libElement)
        self.root.findall('.instances')[0].append(instanceElement)

    def _addSource(self, sourceObject):
        sourceElement = ET.Element("source")
        if sourceObject.filename is not None:
            sourceElement.attrib['filename'] = sourceObject.filename
        if sourceObject.name is not None:
            if sourceObject.name.find("temp_master")!=0:
                # do not save temporary source names
                sourceElement.attrib['name'] = sourceObject.name
        if sourceObject.familyName is not None:
            sourceElement.attrib['familyname'] = sourceObject.familyName
        if sourceObject.styleName is not None:
            sourceElement.attrib['stylename'] = sourceObject.styleName
        if sourceObject.layerName is not None:
            sourceElement.attrib['layer'] = sourceObject.layerName
        if sourceObject.copyLib:
            libElement = ET.Element('lib')
            libElement.attrib['copy'] = "1"
            sourceElement.append(libElement)
        if sourceObject.copyGroups:
            groupsElement = ET.Element('groups')
            groupsElement.attrib['copy'] = "1"
            sourceElement.append(groupsElement)
        if sourceObject.copyFeatures:
            featuresElement = ET.Element('features')
            featuresElement.attrib['copy'] = "1"
            sourceElement.append(featuresElement)
        if sourceObject.copyInfo or sourceObject.muteInfo:
            infoElement = ET.Element('info')
            if sourceObject.copyInfo:
                infoElement.attrib['copy'] = "1"
            if sourceObject.muteInfo:
                infoElement.attrib['mute'] = "1"
            sourceElement.append(infoElement)
        if sourceObject.muteKerning:
            kerningElement = ET.Element("kerning")
            kerningElement.attrib["mute"] = '1'
            sourceElement.append(kerningElement)
        if sourceObject.mutedGlyphNames:
            for name in sourceObject.mutedGlyphNames:
                glyphElement = ET.Element("glyph")
                glyphElement.attrib["name"] = name
                glyphElement.attrib["mute"] = '1'
                sourceElement.append(glyphElement)
        locationElement, sourceObject.location = self._makeLocationElement(sourceObject.location)
        sourceElement.append(locationElement)
        self.root.findall('.sources')[0].append(sourceElement)

    def _addLib(self, dict):
        libElement = ET.Element('lib')
        libElement.append(to_plist(dict))
        self.root.append(libElement)

    def _writeGlyphElement(self, instanceElement, instanceObject, glyphName, data):
        glyphElement = ET.Element('glyph')
        if data.get('mute'):
            glyphElement.attrib['mute'] = "1"
        if data.get('unicodes') is not None:
            glyphElement.attrib['unicode'] = " ".join([hex(u) for u in data.get('unicodes')])
        if data.get('instanceLocation') is not None:
            locationElement, data['instanceLocation'] = self._makeLocationElement(data.get('instanceLocation'))
            glyphElement.append(locationElement)
        if glyphName is not None:
            glyphElement.attrib['name'] = glyphName
        if data.get('note') is not None:
            noteElement = ET.Element('note')
            noteElement.text = data.get('note')
            glyphElement.append(noteElement)
        if data.get('masters') is not None:
            mastersElement = ET.Element("masters")
            for m in data.get('masters'):
                masterElement = ET.Element("master")
                if m.get('glyphName') is not None:
                    masterElement.attrib['glyphname'] = m.get('glyphName')
                if m.get('font') is not None:
                    masterElement.attrib['source'] = m.get('font')
                if m.get('location') is not None:
                    locationElement, m['location'] = self._makeLocationElement(m.get('location'))
                    masterElement.append(locationElement)
                mastersElement.append(masterElement)
            glyphElement.append(mastersElement)
        return glyphElement


class BaseDocReader(object):
    ruleDescriptorClass = RuleDescriptor
    axisDescriptorClass = AxisDescriptor
    sourceDescriptorClass = SourceDescriptor
    instanceDescriptorClass = InstanceDescriptor

    def __init__(self, documentPath, documentObject):
        self.path = documentPath
        self.documentObject = documentObject
        tree = ET.parse(self.path)
        self.root = tree.getroot()
        self.documentObject.formatVersion = self.root.attrib.get("format", "3.0")
        self._axes = []
        self.rules = []
        self.sources = []
        self.instances = []
        self.axisDefaults = {}
        self._strictAxisNames = True

    def read(self):
        self.readAxes()
        self.readRules()
        self.readSources()
        self.readInstances()
        self.readLib()

    def getSourcePaths(self, makeGlyphs=True, makeKerning=True, makeInfo=True):
        paths = []
        for name in self.documentObject.sources.keys():
            paths.append(self.documentObject.sources[name][0].path)
        return paths

    def readRules(self):
        # we also need to read any conditions that are outside of a condition set.
        rules = []
        for ruleElement in self.root.findall(".rules/rule"):
            ruleObject = self.ruleDescriptorClass()
            ruleObject.name = ruleElement.attrib.get("name")
            # read any stray conditions outside a condition set
            externalConditions = self._readConditionElements(ruleElement)
            if externalConditions:
                ruleObject.conditionSets.append(externalConditions)
                warnings.warn('Found stray rule conditions outside a conditionset. Wrapped them in a new conditionset.')
            # read the conditionsets
            for conditionSetElement in ruleElement.findall('.conditionset'):
                conditionSet = self._readConditionElements(conditionSetElement)
                if conditionSet is not None:
                    ruleObject.conditionSets.append(conditionSet)
            for subElement in ruleElement.findall('.sub'):
                a = subElement.attrib['name']
                b = subElement.attrib['with']
                ruleObject.subs.append((a,b))
            rules.append(ruleObject)
        self.documentObject.rules = rules

    def _readConditionElements(self, parentElement):
        cds = []
        for conditionElement in parentElement.findall('.condition'):
            cd = {}
            cdMin = conditionElement.attrib.get("minimum")
            if cdMin is not None:
                cd['minimum'] = float(cdMin)
            else:
                # will allow these to be None, assume axis.minimum
                cd['minimum'] = None
            cdMax = conditionElement.attrib.get("maximum")
            if cdMax is not None:
                cd['maximum'] = float(cdMax)
            else:
                # will allow these to be None, assume axis.maximum
                cd['maximum'] = None
            cd['name'] = conditionElement.attrib.get("name")
            # # test for things
            # if cd.get('minimum') is None and cd.get('maximum') is None:
            #     if ruleObject.name is not None:
            #         n = ruleObject.name
            #     else:
            #         n = "%d" % len(rules)
            #     raise DesignSpaceDocumentError("No minimum or maximum defined in rule \"%s\"." % n)
            cds.append(cd)
        return cds

    def readAxes(self):
        # read the axes elements, including the warp map.
        axes = []
        if len(self.root.findall(".axes/axis"))==0:
            self._strictAxisNames = False
            return
        for axisElement in self.root.findall(".axes/axis"):
            axisObject = self.axisDescriptorClass()
            axisObject.name = axisElement.attrib.get("name")
            axisObject.minimum = float(axisElement.attrib.get("minimum"))
            axisObject.maximum = float(axisElement.attrib.get("maximum"))
            if axisElement.attrib.get('hidden', False):
                axisObject.hidden = True
            axisObject.default = float(axisElement.attrib.get("default"))
            axisObject.tag = axisElement.attrib.get("tag")
            for mapElement in axisElement.findall('map'):
                a = float(mapElement.attrib['input'])
                b = float(mapElement.attrib['output'])
                axisObject.map.append((a,b))
            for labelNameElement in axisElement.findall('labelname'):
                # Note: elementtree reads the xml:lang attribute name as
                # '{http://www.w3.org/XML/1998/namespace}lang'
                for key, lang in labelNameElement.items():
                    labelName = labelNameElement.text
                    axisObject.labelNames[lang] = labelName
            self.documentObject.axes.append(axisObject)
            self.axisDefaults[axisObject.name] = axisObject.default
        self.documentObject.defaultLoc = self.axisDefaults

    def readSources(self):
        for sourceCount, sourceElement in enumerate(self.root.findall(".sources/source")):
            filename = sourceElement.attrib.get('filename')
            if filename is not None and self.path is not None:
                sourcePath = os.path.abspath(os.path.join(os.path.dirname(self.path), filename))
            else:
                sourcePath = None
            sourceName = sourceElement.attrib.get('name')
            if sourceName is None:
                # add a temporary source name
                sourceName = "temp_master.%d" % (sourceCount)
            sourceObject = self.sourceDescriptorClass()
            sourceObject.path = sourcePath        # absolute path to the ufo source
            sourceObject.filename = filename      # path as it is stored in the document
            sourceObject.name = sourceName
            familyName = sourceElement.attrib.get("familyname")
            if familyName is not None:
                sourceObject.familyName = familyName
            styleName = sourceElement.attrib.get("stylename")
            if styleName is not None:
                sourceObject.styleName = styleName
            sourceObject.location = self.locationFromElement(sourceElement)
            layerName = sourceElement.attrib.get('layer')
            if layerName is not None:
                sourceObject.layerName = layerName
            for libElement in sourceElement.findall('.lib'):
                if libElement.attrib.get('copy') == '1':
                    sourceObject.copyLib = True
            for groupsElement in sourceElement.findall('.groups'):
                if groupsElement.attrib.get('copy') == '1':
                    sourceObject.copyGroups = True
            for infoElement in sourceElement.findall(".info"):
                if infoElement.attrib.get('copy') == '1':
                    sourceObject.copyInfo = True
                if infoElement.attrib.get('mute') == '1':
                    sourceObject.muteInfo = True
            for featuresElement in sourceElement.findall(".features"):
                if featuresElement.attrib.get('copy') == '1':
                    sourceObject.copyFeatures = True
            for glyphElement in sourceElement.findall(".glyph"):
                glyphName = glyphElement.attrib.get('name')
                if glyphName is None:
                    continue
                if glyphElement.attrib.get('mute') == '1':
                    sourceObject.mutedGlyphNames.append(glyphName)
            for kerningElement in sourceElement.findall(".kerning"):
                if kerningElement.attrib.get('mute') == '1':
                    sourceObject.muteKerning = True
            self.documentObject.sources.append(sourceObject)

    def locationFromElement(self, element):
        elementLocation = None
        for locationElement in element.findall('.location'):
            elementLocation = self.readLocationElement(locationElement)
            break
        return elementLocation

    def readLocationElement(self, locationElement):
        """ Format 0 location reader """
        if not self.documentObject.axes:
           raise DesignSpaceDocumentError("No axes defined.")
        loc = {}
        for dimensionElement in locationElement.findall(".dimension"):
            dimName = dimensionElement.attrib.get("name")
            if self._strictAxisNames and dimName not in self.axisDefaults:
                # In case the document contains no axis definitions,
                warnings.warn("Location with undefined axis: \"%s\"." % dimName)
                continue
            xValue = yValue = None
            try:
                xValue = dimensionElement.attrib.get('xvalue')
                xValue = float(xValue)
            except ValueError:
                self.logger.info("KeyError in readLocation xValue %3.3f", xValue)
            try:
                yValue = dimensionElement.attrib.get('yvalue')
                if yValue is not None:
                    yValue = float(yValue)
            except ValueError:
                pass
            if yValue is not None:
                loc[dimName] = (xValue, yValue)
            else:
                loc[dimName] = xValue
        return loc

    def readInstances(self, makeGlyphs=True, makeKerning=True, makeInfo=True):
        instanceElements = self.root.findall('.instances/instance')
        for instanceElement in self.root.findall('.instances/instance'):
            self._readSingleInstanceElement(instanceElement, makeGlyphs=makeGlyphs, makeKerning=makeKerning, makeInfo=makeInfo)

    def _readSingleInstanceElement(self, instanceElement, makeGlyphs=True, makeKerning=True, makeInfo=True):
        filename = instanceElement.attrib.get('filename')
        if filename is not None:
            instancePath = os.path.join(os.path.dirname(self.documentObject.path), filename)
            filenameTokenForResults = os.path.basename(filename)
        else:
            instancePath = None
        instanceObject = self.instanceDescriptorClass()
        instanceObject.path = instancePath    # absolute path to the instance
        instanceObject.filename = filename    # path as it is stored in the document
        name = instanceElement.attrib.get("name")
        if name is not None:
            instanceObject.name = name
        familyname = instanceElement.attrib.get('familyname')
        if familyname is not None:
            instanceObject.familyName = familyname
        stylename = instanceElement.attrib.get('stylename')
        if stylename is not None:
            instanceObject.styleName = stylename
        postScriptFontName = instanceElement.attrib.get('postscriptfontname')
        if postScriptFontName is not None:
            instanceObject.postScriptFontName = postScriptFontName
        styleMapFamilyName = instanceElement.attrib.get('stylemapfamilyname')
        if styleMapFamilyName is not None:
            instanceObject.styleMapFamilyName = styleMapFamilyName
        styleMapStyleName = instanceElement.attrib.get('stylemapstylename')
        if styleMapStyleName is not None:
            instanceObject.styleMapStyleName = styleMapStyleName
        # read localised names
        for styleNameElement in instanceElement.findall('stylename'):
            for key, lang in styleNameElement.items():
                styleName = styleNameElement.text
                instanceObject.setStyleName(styleName, lang)
        for familyNameElement in instanceElement.findall('familyname'):
            for key, lang in familyNameElement.items():
                familyName = familyNameElement.text
                instanceObject.setFamilyName(familyName, lang)
        for styleMapStyleNameElement in instanceElement.findall('stylemapstylename'):
            for key, lang in styleMapStyleNameElement.items():
                styleMapStyleName = styleMapStyleNameElement.text
                instanceObject.setStyleMapStyleName(styleMapStyleName, lang)
        for styleMapFamilyNameElement in instanceElement.findall('stylemapfamilyname'):
            for key, lang in styleMapFamilyNameElement.items():
                styleMapFamilyName = styleMapFamilyNameElement.text
                instanceObject.setStyleMapFamilyName(styleMapFamilyName, lang)
        instanceLocation = self.locationFromElement(instanceElement)
        if instanceLocation is not None:
            instanceObject.location = instanceLocation
        for glyphElement in instanceElement.findall('.glyphs/glyph'):
            self.readGlyphElement(glyphElement, instanceObject)
        for infoElement in instanceElement.findall("info"):
            self.readInfoElement(infoElement, instanceObject)
        for libElement in instanceElement.findall('lib'):
            self.readLibElement(libElement, instanceObject)
        self.documentObject.instances.append(instanceObject)

    def readLibElement(self, libElement, instanceObject):
        """Read the lib element for the given instance."""
        instanceObject.lib = from_plist(libElement[0])

    def readInfoElement(self, infoElement, instanceObject):
        """ Read the info element."""
        infoLocation = self.locationFromElement(infoElement)
        instanceObject.info = True

    def readKerningElement(self, kerningElement, instanceObject):
        """ Read the kerning element."""
        kerningLocation = self.locationFromElement(kerningElement)
        instanceObject.addKerning(kerningLocation)

    def readGlyphElement(self, glyphElement, instanceObject):
        """
        Read the glyph element.
            <glyph name="b" unicode="0x62"/>
            <glyph name="b"/>
            <glyph name="b">
                <master location="location-token-bbb" source="master-token-aaa2"/>
                <master glyphname="b.alt1" location="location-token-ccc" source="master-token-aaa3"/>
                <note>
                    This is an instance from an anisotropic interpolation.
                </note>
            </glyph>
        """
        glyphData = {}
        glyphName = glyphElement.attrib.get('name')
        if glyphName is None:
            raise DesignSpaceDocumentError("Glyph object without name attribute.")
        mute = glyphElement.attrib.get("mute")
        if mute == "1":
            glyphData['mute'] = True
        # unicode
        unicodes = glyphElement.attrib.get('unicode')
        if unicodes is not None:
            try:
                unicodes = [int(u, 16) for u in unicodes.split(" ")]
                glyphData['unicodes'] = unicodes
            except ValueError:
                raise DesignSpaceDocumentError("unicode values %s are not integers" % unicodes)

        note = None
        for noteElement in glyphElement.findall('.note'):
            glyphData['note'] = noteElement.text
            break
        instanceLocation = self.locationFromElement(glyphElement)
        if instanceLocation is not None:
            glyphData['instanceLocation'] = instanceLocation
        glyphSources = None
        for masterElement in glyphElement.findall('.masters/master'):
            fontSourceName = masterElement.attrib.get('source')
            sourceLocation = self.locationFromElement(masterElement)
            masterGlyphName = masterElement.attrib.get('glyphname')
            if masterGlyphName is None:
                # if we don't read a glyphname, use the one we have
                masterGlyphName = glyphName
            d = dict(font=fontSourceName,
                     location=sourceLocation,
                     glyphName=masterGlyphName)
            if glyphSources is None:
                glyphSources = []
            glyphSources.append(d)
        if glyphSources is not None:
            glyphData['masters'] = glyphSources
        instanceObject.glyphs[glyphName] = glyphData

    def readLib(self):
        """Read the lib element for the whole document."""
        for libElement in self.root.findall(".lib"):
            self.documentObject.lib = from_plist(libElement[0])


class DesignSpaceDocument(object):
    """ Read, write data from the designspace file"""
    def __init__(self, readerClass=None, writerClass=None):
        self.logger = logging.getLogger("DesignSpaceDocumentLog")
        self.path = None
        self.filename = None
        """String, optional. When the document is read from the disk, this is
        its original file name, i.e. the last part of its path.

        When the document is produced by a Python script and still only exists
        in memory, the producing script can write here an indication of a
        possible "good" filename, in case one wants to save the file somewhere.
        """

        self.formatVersion = None
        self.sources = []
        self.instances = []
        self.axes = []
        self.rules = []
        self.default = None         # name of the default master
        self.defaultLoc = None

        self.lib = {}
        """Custom data associated with the whole document."""

        #
        if readerClass is not None:
            self.readerClass = readerClass
        else:
            self.readerClass = BaseDocReader
        if writerClass is not None:
            self.writerClass = writerClass
        else:
            self.writerClass = BaseDocWriter

    def read(self, path):
        self.path = path
        self.filename = os.path.basename(path)
        reader = self.readerClass(path, self)
        reader.read()
        if self.sources:
            self.findDefault()

    def write(self, path):
        self.path = path
        self.filename = os.path.basename(path)
        self.updatePaths()
        writer = self.writerClass(path, self)
        writer.write()

    def _posixRelativePath(self, otherPath):
        relative = os.path.relpath(otherPath, os.path.dirname(self.path))
        return posix(relative)

    def updatePaths(self):
        """
            Right before we save we need to identify and respond to the following situations:
            In each descriptor, we have to do the right thing for the filename attribute.

            case 1.
            descriptor.filename == None
            descriptor.path == None

            -- action:
            write as is, descriptors will not have a filename attr.
            useless, but no reason to interfere.


            case 2.
            descriptor.filename == "../something"
            descriptor.path == None

            -- action:
            write as is. The filename attr should not be touched.


            case 3.
            descriptor.filename == None
            descriptor.path == "~/absolute/path/there"

            -- action:
            calculate the relative path for filename.
            We're not overwriting some other value for filename, it should be fine


            case 4.
            descriptor.filename == '../somewhere'
            descriptor.path == "~/absolute/path/there"

            -- action:
            there is a conflict between the given filename, and the path.
            So we know where the file is relative to the document.
            Can't guess why they're different, we just choose for path to be correct and update filename.


        """
        for descriptor in self.sources + self.instances:
            # check what the relative path really should be?
            expectedFilename = None
            if descriptor.path is not None and self.path is not None:
                expectedFilename = self._posixRelativePath(descriptor.path)

            # 3
            if descriptor.filename is None and descriptor.path is not None and self.path is not None:
                descriptor.filename = self._posixRelativePath(descriptor.path)
                continue

            # 4
            if descriptor.filename is not None and descriptor.path is not None and self.path is not None:
                if descriptor.filename is not expectedFilename:
                    descriptor.filename = expectedFilename

    def addSource(self, sourceDescriptor):
        self.sources.append(sourceDescriptor)

    def addInstance(self, instanceDescriptor):
        self.instances.append(instanceDescriptor)

    def addAxis(self, axisDescriptor):
        self.axes.append(axisDescriptor)

    def addRule(self, ruleDescriptor):
        self.rules.append(ruleDescriptor)

    def newDefaultLocation(self):
        # Without OrderedDict, output XML would be non-deterministic.
        # https://github.com/LettError/designSpaceDocument/issues/10
        loc = collections.OrderedDict()
        for axisDescriptor in self.axes:
            loc[axisDescriptor.name] = axisDescriptor.default
        return loc

    def updateFilenameFromPath(self, masters=True, instances=True, force=False):
        # set a descriptor filename attr from the path and this document path
        # if the filename attribute is not None: skip it.
        if masters:
            for descriptor in self.sources:
                if descriptor.filename is not None and not force:
                    continue
                if self.path is not None:
                    descriptor.filename = self._posixRelativePath(descriptor.path)
        if instances:
            for descriptor in self.instances:
                if descriptor.filename is not None and not force:
                    continue
                if self.path is not None:
                    descriptor.filename = self._posixRelativePath(descriptor.path)

    def newAxisDescriptor(self):
        # Ask the writer class to make us a new axisDescriptor
        return self.writerClass.getAxisDecriptor()

    def newSourceDescriptor(self):
        # Ask the writer class to make us a new sourceDescriptor
        return self.writerClass.getSourceDescriptor()

    def newInstanceDescriptor(self):
        # Ask the writer class to make us a new instanceDescriptor
        return self.writerClass.getInstanceDescriptor()

    def getAxisOrder(self):
        names = []
        for axisDescriptor in self.axes:
            names.append(axisDescriptor.name)
        return names

    def getAxis(self, name):
        for axisDescriptor in self.axes:
            if axisDescriptor.name == name:
                return axisDescriptor
        return None

    def findDefault(self):
        # new default finder
        # take the sourcedescriptor with the location at all the defaults
        self.default = None
        for sourceDescriptor in self.sources:
            if sourceDescriptor.location == self.defaultLoc:
                # we choose you!
                self.default = sourceDescriptor
                return sourceDescriptor
        # failing that, find the sourcedescriptor with the copyInfo flag
        for sourceDescriptor in self.sources:
            names = set()
            if sourceDescriptor.copyInfo:
                # we choose you!
                self.default = sourceDescriptor
                return sourceDescriptor
        # failing that, well, fail. We don't want to guess any more. 
        warnings.warn("Can't find a suitable default location in this document.")
        return None

    def _axesAsDict(self):
        """
            Make the axis data we have available in
        """
        benderAxes = {}
        for axisDescriptor in self.axes:
            d = {
                'name': axisDescriptor.name,
                'tag': axisDescriptor.tag,
                'minimum': axisDescriptor.minimum,
                'maximum': axisDescriptor.maximum,
                'default': axisDescriptor.default,
                'map': axisDescriptor.map,
            }
            benderAxes[axisDescriptor.name] = d
        return benderAxes

    def normalizeLocation(self, location):
        # adapted from fontTools.varlib.models.normalizeLocation because:
        #   - this needs to work with axis names, not tags
        #   - this needs to accomodate anisotropic locations
        #   - the axes are stored differently here, it's just math
        new = {}
        for axis in self.axes:
            if not axis.name in location:
                # skipping this dimension it seems
                continue
            v = location.get(axis.name, axis.default)
            if type(v)==tuple:
                v = v[0]
            if v == axis.default:
                v = 0.0
            elif v < axis.default:
                if axis.default == axis.minimum:
                    v = 0.0
                else:
                    v = (max(v, axis.minimum) - axis.default) / (axis.default - axis.minimum)
            else:
                if axis.default == axis.maximum:
                    v = 0.0
                else:
                    v = (min(v, axis.maximum) - axis.default) / (axis.maximum - axis.default)
            new[axis.name] = v
        return new

    def normalize(self):
        # scale all the locations of all masters and instances to the -1 - 0 - 1 value.
        # we need the axis data to do the scaling, so we do those last.
        # masters
        for item in self.sources:
            item.location = self.normalizeLocation(item.location)
        # instances
        for item in self.instances:
            # glyph masters for this instance
            for name, glyphData in item.glyphs.items():
                glyphData['instanceLocation'] = self.normalizeLocation(glyphData['instanceLocation'])
                for glyphMaster in glyphData['masters']:
                    glyphMaster['location'] = self.normalizeLocation(glyphMaster['location'])
            item.location = self.normalizeLocation(item.location)
        # now the axes
        for axis in self.axes:
            # scale the map first
            newMap = []
            for inputValue, outputValue in axis.map:
                newOutputValue = self.normalizeLocation({axis.name: outputValue}).get(axis.name)
                newMap.append((inputValue, newOutputValue))
            if newMap:
                axis.map = newMap
            # finally the axis values
            minimum = self.normalizeLocation({axis.name:axis.minimum}).get(axis.name)
            maximum = self.normalizeLocation({axis.name:axis.maximum}).get(axis.name)
            default = self.normalizeLocation({axis.name:axis.default}).get(axis.name)
            # and set them in the axis.minimum
            axis.minimum = minimum
            axis.maximum = maximum
            axis.default = default
        # now the rules
        for rule in self.rules:
            newConditionSets = []
            for conditions in rule.conditionSets:
                newConditions = []
                for cond in conditions:
                    if cond.get('minimum') is not None:
                        minimum = self.normalizeLocation({cond['name']:cond['minimum']}).get(cond['name'])
                    else:
                        minimum = None
                    if cond.get('maximum') is not None:
                        maximum = self.normalizeLocation({cond['name']:cond['maximum']}).get(cond['name'])
                    else:
                        maximum = None
                    newConditions.append(dict(name=cond['name'], minimum=minimum, maximum=maximum))
                newConditionSets.append(newConditions)
            rule.conditionSets = newConditionSets
