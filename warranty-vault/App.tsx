import React from 'react';
import { StatusBar } from 'expo-status-bar';
import { useColorScheme } from 'react-native';
import { DarkTheme, DefaultTheme, NavigationContainer } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import type { RootStackParamList } from './src/navigation';
import { RecordsProvider } from './src/state/RecordsContext';
import { HomeScreen } from './src/screens/HomeScreen';
import { DetailScreen } from './src/screens/DetailScreen';
import { AddPhotoScreen } from './src/screens/AddPhotoScreen';
import { FormScreen } from './src/screens/FormScreen';
import { SettingsScreen } from './src/screens/SettingsScreen';
import { PaywallScreen } from './src/screens/PaywallScreen';

const Stack = createNativeStackNavigator<RootStackParamList>();

export default function App() {
  const scheme = useColorScheme();
  return (
    <SafeAreaProvider>
      <RecordsProvider>
        <NavigationContainer theme={scheme === 'dark' ? DarkTheme : DefaultTheme}>
          <StatusBar style="auto" />
          <Stack.Navigator
            screenOptions={{
              headerBackButtonDisplayMode: 'minimal',
            }}
          >
            <Stack.Screen name="Home" component={HomeScreen} options={{ headerShown: false }} />
            <Stack.Screen name="Detail" component={DetailScreen} options={{ title: '詳細' }} />
            <Stack.Screen
              name="AddPhoto"
              component={AddPhotoScreen}
              options={{ title: '家電を追加する', presentation: 'modal' }}
            />
            <Stack.Screen
              name="Form"
              component={FormScreen}
              options={{ title: '内容を確認する' }}
            />
            <Stack.Screen name="Settings" component={SettingsScreen} options={{ title: '設定' }} />
            <Stack.Screen
              name="Paywall"
              component={PaywallScreen}
              options={{ title: '', presentation: 'modal' }}
            />
          </Stack.Navigator>
        </NavigationContainer>
      </RecordsProvider>
    </SafeAreaProvider>
  );
}
