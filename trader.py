from typing import Dict, List
import json
import statistics
from datamodel import OrderDepth, TradingState, Order
import jsonpickle

class Trader:
    def __init__(self):
        self.product_parameters = {
            "RAINFOREST_RESIN": {
                "position_limit": 50,
                "fair_value": 10000,  # Stable value
                "spread": 2
            },
            "KELP": {
                "position_limit": 50,
                "fair_value": None,  # Dynamic value
                "spread": 4
            },
            "SQUID_INK": {
                "position_limit": 50,
                "fair_value": None,  # Dynamic value
                "spread": 6,  # Wider spread due to volatility
                "mean_reversion_window": 10,  # Window for mean reversion
                "std_dev_threshold": 2.0  # Number of std devs for mean reversion signals
            }
        }
        self.historical_prices = {}
        self.ema_short = {}  # Short-term exponential moving average
        self.ema_long = {}   # Long-term exponential average
        
    def calculate_fair_value(self, product: str, order_depth: OrderDepth, market_trades: List) -> float:
        """Calculate fair value using weighted average of market trades and mid price"""
        # Get mid price from order book
        best_bid = max(order_depth.buy_orders.keys()) if order_depth.buy_orders else None
        best_ask = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else None
        
        if best_bid and best_ask:
            mid_price = (best_bid + best_ask) / 2
        else:
            mid_price = self.product_parameters[product]["fair_value"]
            
        # Calculate VWAP from recent trades
        if market_trades and len(market_trades) > 0:
            total_volume = sum(abs(trade.quantity) for trade in market_trades)
            vwap = sum(trade.price * abs(trade.quantity) for trade in market_trades) / total_volume
            
            # Combine VWAP and mid price with weights
            if mid_price:
                fair_value = (vwap * 0.7) + (mid_price * 0.3)
            else:
                fair_value = vwap
        else:
            fair_value = mid_price if mid_price else self.product_parameters[product]["fair_value"]
            
        return fair_value

    def update_ema(self, product: str, price: float, short_period=9, long_period=21):
        """Update exponential moving averages"""
        if product not in self.ema_short:
            self.ema_short[product] = price
            self.ema_long[product] = price
        else:
            # Update EMAs
            self.ema_short[product] = (price * (2 / (short_period + 1))) + (self.ema_short[product] * (1 - (2 / (short_period + 1))))
            self.ema_long[product] = (price * (2 / (long_period + 1))) + (self.ema_long[product] * (1 - (2 / (long_period + 1))))

    def get_order_volume(self, product: str, position: int, side: str) -> int:
        """Calculate order volume respecting position limits"""
        position_limit = self.product_parameters[product]["position_limit"]
        
        if side == "BUY":
            return min(position_limit - position, 10)  # Don't take full position at once
        else:  # SELL
            return min(position_limit + position, 10)  # Don't take full position at once

    def check_mean_reversion_signal(self, product: str, current_price: float) -> str:
        """Check for mean reversion signals for SQUID_INK"""
        if product != "SQUID_INK" or not self.historical_prices.get(product):
            return None
            
        window = self.product_parameters[product]["mean_reversion_window"]
        threshold = self.product_parameters[product]["std_dev_threshold"]
        
        # Get recent prices
        recent_prices = self.historical_prices[product][-window:]
        if len(recent_prices) < window:
            return None
            
        mean = statistics.mean(recent_prices)
        std_dev = statistics.stdev(recent_prices)
        
        # Calculate z-score
        z_score = (current_price - mean) / std_dev if std_dev > 0 else 0
        
        # Generate signals based on deviation
        if z_score > threshold:
            return "SELL"  # Price is too high, expect reversion down
        elif z_score < -threshold:
            return "BUY"   # Price is too low, expect reversion up
            
        return None

    def run(self, state: TradingState):
        """
        Main trading logic
        """
        result = {product: [] for product in state.order_depths.keys()}
        
        # Restore state if exists
        if state.traderData != "":
            saved_state = jsonpickle.decode(state.traderData)
            self.historical_prices = saved_state["historical_prices"]
            self.ema_short = saved_state["ema_short"]
            self.ema_long = saved_state["ema_long"]
            
        for product in state.order_depths:
            if product not in state.order_depths:
                continue
                
            order_depth = state.order_depths[product]
            orders: List[Order] = []
            
            # Calculate fair value
            fair_value = self.calculate_fair_value(
                product, 
                order_depth,
                state.market_trades.get(product, [])
            )
            
            # Update EMAs and historical prices
            if fair_value:
                self.update_ema(product, fair_value)
                if product not in self.historical_prices:
                    self.historical_prices[product] = []
                self.historical_prices[product].append(fair_value)
            
            position = state.position.get(product, 0)
            spread = self.product_parameters[product]["spread"]
            
            # Different strategies per product
            if product == "RAINFOREST_RESIN":
                # Simple market making for stable product
                if len(order_depth.sell_orders) > 0:
                    best_ask = min(order_depth.sell_orders.keys())
                    if best_ask < fair_value - spread:
                        buy_volume = self.get_order_volume(product, position, "BUY")
                        orders.append(Order(product, best_ask, buy_volume))

                if len(order_depth.buy_orders) > 0:
                    best_bid = max(order_depth.buy_orders.keys())
                    if best_bid > fair_value + spread:
                        sell_volume = self.get_order_volume(product, position, "SELL")
                        orders.append(Order(product, best_bid, -sell_volume))
                        
            elif product == "KELP":
                # Trend following strategy
                if product in self.ema_short and product in self.ema_long:
                    # Buy on uptrend
                    if self.ema_short[product] > self.ema_long[product] and position < self.product_parameters[product]["position_limit"]:
                        if len(order_depth.sell_orders) > 0:
                            best_ask = min(order_depth.sell_orders.keys())
                            buy_volume = self.get_order_volume(product, position, "BUY")
                            orders.append(Order(product, best_ask, buy_volume))
                    
                    # Sell on downtrend
                    elif self.ema_short[product] < self.ema_long[product] and position > -self.product_parameters[product]["position_limit"]:
                        if len(order_depth.buy_orders) > 0:
                            best_bid = max(order_depth.buy_orders.keys())
                            sell_volume = self.get_order_volume(product, position, "SELL")
                            orders.append(Order(product, best_bid, -sell_volume))
                            
            elif product == "SQUID_INK":
                # Mean reversion strategy
                signal = self.check_mean_reversion_signal(product, fair_value)
                
                if signal == "BUY" and len(order_depth.sell_orders) > 0:
                    best_ask = min(order_depth.sell_orders.keys())
                    buy_volume = self.get_order_volume(product, position, "BUY")
                    orders.append(Order(product, best_ask, buy_volume))
                    
                elif signal == "SELL" and len(order_depth.buy_orders) > 0:
                    best_bid = max(order_depth.buy_orders.keys())
                    sell_volume = self.get_order_volume(product, position, "SELL")
                    orders.append(Order(product, best_bid, -sell_volume))
            
            result[product] = orders

        # Save state
        state_to_save = {
            "historical_prices": self.historical_prices,
            "ema_short": self.ema_short,
            "ema_long": self.ema_long
        }
        
        return result, 0, jsonpickle.encode(state_to_save) 