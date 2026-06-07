// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import React, { useState } from 'react';
import { Box, Tabs, Tab } from '@mui/material';
import AccountTreeOutlinedIcon from '@mui/icons-material/AccountTreeOutlined';
import BoltOutlinedIcon from '@mui/icons-material/BoltOutlined';
import HandymanOutlinedIcon from '@mui/icons-material/HandymanOutlined';
import ToolsPage from './ToolsPage';
import WorkflowsPage from './WorkflowsPage';
import TriggersPage from './TriggersPage';
import { ghostTabsSx } from '../theme/ghostStyles';

interface TabPanelProps {
  children?: React.ReactNode;
  index: number;
  value: number;
}

function TabPanel(props: TabPanelProps) {
  const { children, value, index, ...other } = props;

  return (
    <div
      role="tabpanel"
      hidden={value !== index}
      id={`automations-tabpanel-${index}`}
      aria-labelledby={`automations-tab-${index}`}
      {...other}
    >
      {value === index && <Box>{children}</Box>}
    </div>
  );
}

const WorkflowSystemPage: React.FC = () => {
  const [tabValue, setTabValue] = useState(0);

  const handleTabChange = (_event: React.SyntheticEvent, newValue: number) => {
    setTabValue(newValue);
  };

  return (
    <Box>
      <Box sx={{ borderBottom: 1, borderColor: 'rgba(255, 255, 255, 0.06)', position: 'sticky', top: 0, bgcolor: 'transparent', zIndex: 1 }}>
        <Tabs
          value={tabValue}
          onChange={handleTabChange}
          aria-label="automations tabs"
          centered
          sx={ghostTabsSx}
        >
          <Tab icon={<AccountTreeOutlinedIcon />} iconPosition="start" label="Workflows" />
          <Tab icon={<BoltOutlinedIcon />} iconPosition="start" label="Triggers" />
          <Tab icon={<HandymanOutlinedIcon />} iconPosition="start" label="Tools" />
        </Tabs>
      </Box>

      <TabPanel value={tabValue} index={0}>
        <WorkflowsPage />
      </TabPanel>
      <TabPanel value={tabValue} index={1}>
        <TriggersPage />
      </TabPanel>
      <TabPanel value={tabValue} index={2}>
        <ToolsPage />
      </TabPanel>
    </Box>
  );
};

export default WorkflowSystemPage;
