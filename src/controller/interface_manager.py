    def _setup_commands(self):
        """Setup available commands"""
        self.commands = {
            'help': self._cmd_help,
            'status': self._cmd_status,
            'list': self._cmd_list,
            'start': self._cmd_start,
            'stop': self._cmd_stop,
            'restart': self._cmd_restart,
            'config': self._cmd_config,
            'health': self._cmd_health,
            'exit': self._cmd_exit,
            'quit': self._cmd_exit
        }
        
        # Add command help text
        self.command_help = {
            'help': 'Show this help message',
            'status': 'Show system status',
            'list': 'List all modules',
            'start': 'Start a module (usage: start <module_id>)',
            'stop': 'Stop a module (usage: stop <module_id>)',
            'restart': 'Restart a module (usage: restart <module_id>)',
            'config': 'Show module configuration (usage: config <module_id>)',
            'health': 'Show health information (usage: health [history] [--module <id>] [--metric <name>] [--limit <n>] [--window <seconds>])',
            'exit': 'Exit the interface',
            'quit': 'Exit the interface'
        }

    def _cmd_health(self, args):
        """Handle health command"""
        if not args:
            # Show current health summary
            summary = self.health_monitor.get_health_summary()
            print("\nSystem Health Summary:")
            print(f"Total modules: {summary['total_modules']}")
            print(f"Online modules: {summary['online_modules']}")
            print(f"Offline modules: {summary['offline_modules']}")
            
            if summary['average_metrics']:
                print("\nAverage metrics across all online modules:")
                for metric, value in summary['average_metrics'].items():
                    print(f"  {metric}: {value:.2f}")
            return

        # Parse health subcommands and options
        import argparse
        parser = argparse.ArgumentParser(description='Health monitoring commands')
        parser.add_argument('subcommand', nargs='?', default='summary', help='Health subcommand')
        parser.add_argument('--module', help='Specific module ID to show history for')
        parser.add_argument('--metric', help='Specific metric to show stats for')
        parser.add_argument('--limit', type=int, help='Number of records to show')
        parser.add_argument('--window', type=int, default=3600, 
                          help='Time window in seconds for stats (default: 3600)')
        
        try:
            args = parser.parse_args(args)
        except SystemExit:
            return

        if args.subcommand == 'history':
            if args.module:
                # Print history for specific module
                history = self.health_monitor.get_module_health_history(args.module, args.limit)
                if not history:
                    print(f"No history found for module {args.module}")
                    return
                    
                if args.metric:
                    # Print stats for specific metric
                    stats = self.health_monitor.get_module_health_stats(args.module, args.metric, args.window)
                    if not stats:
                        print(f"No {args.metric} data found for module {args.module} in the last {args.window} seconds")
                        return
                        
                    print(f"\n{args.metric} statistics for module {args.module} (last {args.window} seconds):")
                    print(f"  Min: {stats['min']}")
                    print(f"  Max: {stats['max']}")
                    print(f"  Avg: {stats['avg']:.2f}")
                    print(f"  Latest: {stats['latest']}")
                    print(f"  Samples: {stats['samples']}")
                else:
                    # Print full history
                    print(f"\nHealth history for module {args.module}:")
                    for record in reversed(history):  # Most recent first
                        timestamp = datetime.fromtimestamp(record['timestamp']).strftime('%Y-%m-%d %H:%M:%S')
                        print(f"\n{timestamp}:")
                        for key, value in record.items():
                            if key != 'timestamp':
                                print(f"  {key}: {value}")
            else:
                print("Error: --module is required for history command")
        else:
            print(f"Unknown health subcommand: {args.subcommand}")
            print("Available subcommands: history") 