// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Curated MUI icon registry for dynamic icon lookup by name string.
 *
 * Used by `utils/icons.ts` (`getMuiIcon`) and `components/TemplateIconPicker.tsx`
 * to resolve template / domain / tool icon names at runtime. Components that
 * reference a specific icon statically should still `import Foo from
 * '@mui/icons-material/Foo'` directly — this registry is only for name-string
 * lookup.
 *
 * Both the raw and the `Outlined` variants of every entry are registered
 * because `getMuiIcon` maps names to their Outlined form for a consistent
 * stroked look across the app.
 *
 * Adding a new icon: add two `import` lines (raw + Outlined) and two
 * entries to `ICON_REGISTRY`. An ESLint rule bans the barrel import
 * (`from '@mui/icons-material'`) to prevent silent bundle bloat.
 */

import type { SvgIconComponent } from '@mui/icons-material';
import AccountBalance from '@mui/icons-material/AccountBalance';
import AccountBalanceOutlined from '@mui/icons-material/AccountBalanceOutlined';
import AccountTree from '@mui/icons-material/AccountTree';
import AccountTreeOutlined from '@mui/icons-material/AccountTreeOutlined';
import Api from '@mui/icons-material/Api';
import ApiOutlined from '@mui/icons-material/ApiOutlined';
import ArrowForward from '@mui/icons-material/ArrowForward';
import ArrowForwardOutlined from '@mui/icons-material/ArrowForwardOutlined';
import Article from '@mui/icons-material/Article';
import ArticleOutlined from '@mui/icons-material/ArticleOutlined';
import AttachMoney from '@mui/icons-material/AttachMoney';
import AttachMoneyOutlined from '@mui/icons-material/AttachMoneyOutlined';
import AutoAwesome from '@mui/icons-material/AutoAwesome';
import AutoAwesomeOutlined from '@mui/icons-material/AutoAwesomeOutlined';
import Biotech from '@mui/icons-material/Biotech';
import BiotechOutlined from '@mui/icons-material/BiotechOutlined';
import Build from '@mui/icons-material/Build';
import BuildOutlined from '@mui/icons-material/BuildOutlined';
import Business from '@mui/icons-material/Business';
import BusinessOutlined from '@mui/icons-material/BusinessOutlined';
import CalendarMonth from '@mui/icons-material/CalendarMonth';
import CalendarMonthOutlined from '@mui/icons-material/CalendarMonthOutlined';
import Campaign from '@mui/icons-material/Campaign';
import CampaignOutlined from '@mui/icons-material/CampaignOutlined';
import Category from '@mui/icons-material/Category';
import CategoryOutlined from '@mui/icons-material/CategoryOutlined';
import Celebration from '@mui/icons-material/Celebration';
import CelebrationOutlined from '@mui/icons-material/CelebrationOutlined';
import Church from '@mui/icons-material/Church';
import ChurchOutlined from '@mui/icons-material/ChurchOutlined';
import Circle from '@mui/icons-material/Circle';
import CircleOutlined from '@mui/icons-material/CircleOutlined';
import Code from '@mui/icons-material/Code';
import CodeOutlined from '@mui/icons-material/CodeOutlined';
import Coronavirus from '@mui/icons-material/Coronavirus';
import CoronavirusOutlined from '@mui/icons-material/CoronavirusOutlined';
import CorporateFare from '@mui/icons-material/CorporateFare';
import CorporateFareOutlined from '@mui/icons-material/CorporateFareOutlined';
import Create from '@mui/icons-material/Create';
import CreateOutlined from '@mui/icons-material/CreateOutlined';
import DataObject from '@mui/icons-material/DataObject';
import DataObjectOutlined from '@mui/icons-material/DataObjectOutlined';
import Description from '@mui/icons-material/Description';
import DescriptionOutlined from '@mui/icons-material/DescriptionOutlined';
import EmojiEvents from '@mui/icons-material/EmojiEvents';
import EmojiEventsOutlined from '@mui/icons-material/EmojiEventsOutlined';
import Event from '@mui/icons-material/Event';
import EventOutlined from '@mui/icons-material/EventOutlined';
import Explore from '@mui/icons-material/Explore';
import ExploreOutlined from '@mui/icons-material/ExploreOutlined';
import Face from '@mui/icons-material/Face';
import FaceOutlined from '@mui/icons-material/FaceOutlined';
import FamilyRestroom from '@mui/icons-material/FamilyRestroom';
import FamilyRestroomOutlined from '@mui/icons-material/FamilyRestroomOutlined';
import FileDownload from '@mui/icons-material/FileDownload';
import FileDownloadOutlined from '@mui/icons-material/FileDownloadOutlined';
import Flag from '@mui/icons-material/Flag';
import FlagOutlined from '@mui/icons-material/FlagOutlined';
import FormatQuote from '@mui/icons-material/FormatQuote';
import FormatQuoteOutlined from '@mui/icons-material/FormatQuoteOutlined';
import Functions from '@mui/icons-material/Functions';
import FunctionsOutlined from '@mui/icons-material/FunctionsOutlined';
import Gavel from '@mui/icons-material/Gavel';
import GavelOutlined from '@mui/icons-material/GavelOutlined';
import Groups from '@mui/icons-material/Groups';
import GroupsOutlined from '@mui/icons-material/GroupsOutlined';
import Handshake from '@mui/icons-material/Handshake';
import HandshakeOutlined from '@mui/icons-material/HandshakeOutlined';
import Healing from '@mui/icons-material/Healing';
import HealingOutlined from '@mui/icons-material/HealingOutlined';
import Home from '@mui/icons-material/Home';
import HomeOutlined from '@mui/icons-material/HomeOutlined';
import HowToVote from '@mui/icons-material/HowToVote';
import HowToVoteOutlined from '@mui/icons-material/HowToVoteOutlined';
import Hub from '@mui/icons-material/Hub';
import HubOutlined from '@mui/icons-material/HubOutlined';
import Insights from '@mui/icons-material/Insights';
import InsightsOutlined from '@mui/icons-material/InsightsOutlined';
import Inventory2 from '@mui/icons-material/Inventory2';
import Inventory2Outlined from '@mui/icons-material/Inventory2Outlined';
import Label from '@mui/icons-material/Label';
import LabelOutlined from '@mui/icons-material/LabelOutlined';
import LibraryBooks from '@mui/icons-material/LibraryBooks';
import LibraryBooksOutlined from '@mui/icons-material/LibraryBooksOutlined';
import Lightbulb from '@mui/icons-material/Lightbulb';
import LightbulbOutlined from '@mui/icons-material/LightbulbOutlined';
import Link from '@mui/icons-material/Link';
import LinkOutlined from '@mui/icons-material/LinkOutlined';
import LocalHospital from '@mui/icons-material/LocalHospital';
import LocalHospitalOutlined from '@mui/icons-material/LocalHospitalOutlined';
import LocationCity from '@mui/icons-material/LocationCity';
import LocationCityOutlined from '@mui/icons-material/LocationCityOutlined';
import Map from '@mui/icons-material/Map';
import MapOutlined from '@mui/icons-material/MapOutlined';
import Medication from '@mui/icons-material/Medication';
import MedicationOutlined from '@mui/icons-material/MedicationOutlined';
import Memory from '@mui/icons-material/Memory';
import MemoryOutlined from '@mui/icons-material/MemoryOutlined';
import MenuBook from '@mui/icons-material/MenuBook';
import MenuBookOutlined from '@mui/icons-material/MenuBookOutlined';
import MilitaryTech from '@mui/icons-material/MilitaryTech';
import MilitaryTechOutlined from '@mui/icons-material/MilitaryTechOutlined';
import MonitorHeart from '@mui/icons-material/MonitorHeart';
import MonitorHeartOutlined from '@mui/icons-material/MonitorHeartOutlined';
import Newspaper from '@mui/icons-material/Newspaper';
import NewspaperOutlined from '@mui/icons-material/NewspaperOutlined';
import Payments from '@mui/icons-material/Payments';
import PaymentsOutlined from '@mui/icons-material/PaymentsOutlined';
import People from '@mui/icons-material/People';
import PeopleOutlined from '@mui/icons-material/PeopleOutlined';
import Person from '@mui/icons-material/Person';
import PersonOutlined from '@mui/icons-material/PersonOutlined';
import Pets from '@mui/icons-material/Pets';
import PetsOutlined from '@mui/icons-material/PetsOutlined';
import Place from '@mui/icons-material/Place';
import PlaceOutlined from '@mui/icons-material/PlaceOutlined';
import Psychology from '@mui/icons-material/Psychology';
import PsychologyOutlined from '@mui/icons-material/PsychologyOutlined';
import Public from '@mui/icons-material/Public';
import PublicOutlined from '@mui/icons-material/PublicOutlined';
import Receipt from '@mui/icons-material/Receipt';
import ReceiptOutlined from '@mui/icons-material/ReceiptOutlined';
import School from '@mui/icons-material/School';
import SchoolOutlined from '@mui/icons-material/SchoolOutlined';
import Science from '@mui/icons-material/Science';
import ScienceOutlined from '@mui/icons-material/ScienceOutlined';
import Security from '@mui/icons-material/Security';
import SecurityOutlined from '@mui/icons-material/SecurityOutlined';
import Shield from '@mui/icons-material/Shield';
import ShieldOutlined from '@mui/icons-material/ShieldOutlined';
import Storage from '@mui/icons-material/Storage';
import StorageOutlined from '@mui/icons-material/StorageOutlined';
import Terminal from '@mui/icons-material/Terminal';
import TerminalOutlined from '@mui/icons-material/TerminalOutlined';
import Terrain from '@mui/icons-material/Terrain';
import TerrainOutlined from '@mui/icons-material/TerrainOutlined';
import Timeline from '@mui/icons-material/Timeline';
import TimelineOutlined from '@mui/icons-material/TimelineOutlined';
import Topic from '@mui/icons-material/Topic';
import TopicOutlined from '@mui/icons-material/TopicOutlined';
import TrendingUp from '@mui/icons-material/TrendingUp';
import TrendingUpOutlined from '@mui/icons-material/TrendingUpOutlined';
import ViewModule from '@mui/icons-material/ViewModule';
import ViewModuleOutlined from '@mui/icons-material/ViewModuleOutlined';
import Work from '@mui/icons-material/Work';
import WorkOutlined from '@mui/icons-material/WorkOutlined';

export const ICON_REGISTRY: Record<string, SvgIconComponent> = {
  AccountBalance,
  AccountBalanceOutlined,
  AccountTree,
  AccountTreeOutlined,
  Api,
  ApiOutlined,
  ArrowForward,
  ArrowForwardOutlined,
  Article,
  ArticleOutlined,
  AttachMoney,
  AttachMoneyOutlined,
  AutoAwesome,
  AutoAwesomeOutlined,
  Biotech,
  BiotechOutlined,
  Build,
  BuildOutlined,
  Business,
  BusinessOutlined,
  CalendarMonth,
  CalendarMonthOutlined,
  Campaign,
  CampaignOutlined,
  Category,
  CategoryOutlined,
  Celebration,
  CelebrationOutlined,
  Church,
  ChurchOutlined,
  Circle,
  CircleOutlined,
  Code,
  CodeOutlined,
  Coronavirus,
  CoronavirusOutlined,
  CorporateFare,
  CorporateFareOutlined,
  Create,
  CreateOutlined,
  DataObject,
  DataObjectOutlined,
  Description,
  DescriptionOutlined,
  EmojiEvents,
  EmojiEventsOutlined,
  Event,
  EventOutlined,
  Explore,
  ExploreOutlined,
  Face,
  FaceOutlined,
  FamilyRestroom,
  FamilyRestroomOutlined,
  FileDownload,
  FileDownloadOutlined,
  Flag,
  FlagOutlined,
  FormatQuote,
  FormatQuoteOutlined,
  Functions,
  FunctionsOutlined,
  Gavel,
  GavelOutlined,
  Groups,
  GroupsOutlined,
  Handshake,
  HandshakeOutlined,
  Healing,
  HealingOutlined,
  Home,
  HomeOutlined,
  HowToVote,
  HowToVoteOutlined,
  Hub,
  HubOutlined,
  Insights,
  InsightsOutlined,
  Inventory2,
  Inventory2Outlined,
  Label,
  LabelOutlined,
  LibraryBooks,
  LibraryBooksOutlined,
  Lightbulb,
  LightbulbOutlined,
  Link,
  LinkOutlined,
  LocalHospital,
  LocalHospitalOutlined,
  LocationCity,
  LocationCityOutlined,
  Map,
  MapOutlined,
  Medication,
  MedicationOutlined,
  Memory,
  MemoryOutlined,
  MenuBook,
  MenuBookOutlined,
  MilitaryTech,
  MilitaryTechOutlined,
  MonitorHeart,
  MonitorHeartOutlined,
  Newspaper,
  NewspaperOutlined,
  Payments,
  PaymentsOutlined,
  People,
  PeopleOutlined,
  Person,
  PersonOutlined,
  Pets,
  PetsOutlined,
  Place,
  PlaceOutlined,
  Psychology,
  PsychologyOutlined,
  Public,
  PublicOutlined,
  Receipt,
  ReceiptOutlined,
  School,
  SchoolOutlined,
  Science,
  ScienceOutlined,
  Security,
  SecurityOutlined,
  Shield,
  ShieldOutlined,
  Storage,
  StorageOutlined,
  Terminal,
  TerminalOutlined,
  Terrain,
  TerrainOutlined,
  Timeline,
  TimelineOutlined,
  Topic,
  TopicOutlined,
  TrendingUp,
  TrendingUpOutlined,
  ViewModule,
  ViewModuleOutlined,
  Work,
  WorkOutlined,
};

/** Curated icon categories shown by TemplateIconPicker. Each name must be
 *  present in `ICON_REGISTRY`; the picker resolves components from there. */
export const PICKER_CURATED_ICONS: Record<string, string[]> = {
  'People': ['Person', 'People', 'Groups', 'Face', 'FamilyRestroom'],
  'Organizations': ['Business', 'AccountBalance', 'School', 'LocalHospital', 'CorporateFare'],
  'Places': ['Place', 'Public', 'LocationCity', 'Home', 'Terrain', 'Flag', 'Map'],
  'Events': ['Event', 'CalendarMonth', 'Campaign', 'Celebration'],
  'Documents': ['Article', 'Description', 'MenuBook', 'LibraryBooks', 'Newspaper'],
  'Science': ['Science', 'Biotech', 'Coronavirus', 'Medication', 'MonitorHeart', 'Psychology', 'Healing'],
  'Technology': ['Code', 'Memory', 'Api', 'Storage', 'Hub', 'Terminal', 'DataObject', 'Functions', 'ViewModule'],
  'Law & Security': ['Gavel', 'Security', 'Shield'],
  'Finance': ['AttachMoney', 'Payments', 'TrendingUp', 'Receipt'],
  'Concepts': ['Lightbulb', 'Category', 'Topic', 'Label', 'AutoAwesome', 'Insights'],
  'Actions': ['Work', 'Create', 'Link', 'AccountTree', 'Build', 'Handshake', 'Explore'],
  'Other': ['Timeline', 'FormatQuote', 'EmojiEvents', 'Inventory2', 'Pets', 'MilitaryTech', 'HowToVote', 'Church', 'FileDownload'],
};
