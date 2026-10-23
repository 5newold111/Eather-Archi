import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { STATUS_META } from '../lib/warranty';
import { radius, useTheme } from '../theme';
import type { WarrantyStatus } from '../types';

export function StatusBadge({ status }: { status: WarrantyStatus }) {
  const theme = useTheme();
  const colors = theme.statusColors[status];
  return (
    <View style={[styles.badge, { backgroundColor: colors.bg }]}>
      <Text style={[styles.label, { color: colors.fg }]}>{STATUS_META[status].label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  badge: {
    alignSelf: 'flex-start',
    borderRadius: radius.pill,
    paddingVertical: 4,
    paddingHorizontal: 10,
  },
  label: {
    fontSize: 12,
    fontWeight: '700',
  },
});
