// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { createCrudApi, fetchAllPages } from '../crudApiFactory';
import { apiClient } from './client';
import type { Template } from '../../types';

const templateCrudApi = createCrudApi<Template, Partial<Template>, Partial<Template>>('/templates', apiClient);

export const templateApi = {
  list: async (templateType?: string) => {
    // Fetch all pages to get complete template list
    const allTemplates = await fetchAllPages<Template>(
      (page, pageSize) => templateCrudApi.list({
        ...(templateType ? { template_type: templateType } : {}),
        page,
        page_size: pageSize
      }),
      100 // Fetch 100 items per page for efficiency
    );
    return allTemplates;
  },
  get: templateCrudApi.get,
  create: templateCrudApi.create,
  update: templateCrudApi.update,
  delete: async (id: string, force?: boolean) => {
    const params = force ? { force: true } : {};
    await apiClient.delete(`/templates/${id}`, { params });
  },
};
