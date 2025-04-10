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
                "spread": 2,
                "min_edge": 1  # Minimum edge required to trade
            },
            "KELP": {
                "position_limit": 50,
                "fair_value": None,  # Dynamic value
                "spread": 3,
                "momentum_window": 5,  # Window for momentum calculation
                "volatility_window": 20,  # Window for volatility calculation
                "min_edge": 2  # Minimum edge required to trade
            },
            "SQUID_INK": {
                "position_limit": 50,
                "fair_value": None,  # Dynamic value
                "spread": 5,  # Wider spread due to volatility
                "mean_reversion_window": 15,  # Window for mean reversion
                "std_dev_threshold": 1.5,  # Number of std devs for mean reversion signals
                "min_edge": 3  # Minimum edge required to trade
            }
        }
        self.historical_prices = {}
        self.volatility = {}  # Track volatility per product
        self.momentum = {}    # Track momentum per product
        
    def calculate_fair_value(self, product: str, order_depth: OrderDepth) -> float:
        """Calculate fair value using order book data"""
        if not order_depth.buy_orders or not order_depth.sell_orders:
            return self.product_parameters[product]["fair_value"]
            
        # Calculate volume-weighted average prices
        total_bid_volume = 0
        total_bid_value = 0
        total_ask_volume = 0
        total_ask_value = 0
        
        for price, volume in order_depth.buy_orders.items():
            total_bid_volume += abs(volume)
            total_bid_value += price * abs(volume)
            
        for price, volume in order_depth.sell_orders.items():
            total_ask_volume += abs(volume)
            total_ask_value += price * abs(volume)
            
        # Calculate VWAPs
        bid_vwap = total_bid_value / total_bid_volume if total_bid_volume > 0 else None
        ask_vwap = total_ask_value / total_ask_volume if total_ask_volume > 0 else None
        
        if bid_vwap and ask_vwap:
            # Weight more towards the side with more volume
            total_volume = total_bid_volume + total_ask_volume
            bid_weight = total_bid_volume / total_volume
            ask_weight = total_ask_volume / total_volume
            return (bid_vwap * bid_weight) + (ask_vwap * ask_weight)
        elif bid_vwap:
            return bid_vwap
        elif ask_vwap:
            return ask_vwap
        else:
            return self.product_parameters[product]["fair_value"]

    def update_market_metrics(self, product: str, price: float):
        """Update market metrics (volatility, momentum) for the product"""
        if product not in self.historical_prices:
            self.historical_prices[product] = []
        self.historical_prices[product].append(price)
        
        # Update volatility
        window = self.product_parameters[product].get("volatility_window", 20)
        if len(self.historical_prices[product]) >= window:
            recent_prices = self.historical_prices[product][-window:]
            self.volatility[product] = statistics.stdev(recent_prices)
        
        # Update momentum
        window = self.product_parameters[product].get("momentum_window", 5)
        if len(self.historical_prices[product]) >= window:
            recent_prices = self.historical_prices[product][-window:]
            self.momentum[product] = (recent_prices[-1] - recent_prices[0]) / recent_prices[0]

    def get_order_volume(self, product: str, position: int, side: str, available_volume: int) -> int:
        """Calculate order volume with improved risk management"""
        position_limit = self.product_parameters[product]["position_limit"]
        
        # Base volume on current position and available market volume
        if side == "BUY":
            max_position = position_limit - position
            # Scale order size based on volatility if available
            if product in self.volatility:
                vol_scale = max(0.2, 1 - self.volatility[product] / 100)  # Reduce size in high volatility
            else:
                vol_scale = 0.5
                
            base_volume = min(max_position, available_volume, round(5 * vol_scale))
            
            # Further reduce size if near position limit
            position_scale = 1 - (abs(position) / position_limit)
            return max(1, round(base_volume * position_scale))
        else:  # SELL
            max_position = position_limit + position
            if product in self.volatility:
                vol_scale = max(0.2, 1 - self.volatility[product] / 100)
            else:
                vol_scale = 0.5
                
            base_volume = min(max_position, available_volume, round(5 * vol_scale))
            position_scale = 1 - (abs(position) / position_limit)
            return max(1, round(base_volume * position_scale))

    def check_mean_reversion_signal(self, product: str, current_price: float) -> str:
        """Check for mean reversion signals for SQUID_INK"""
        if not self.historical_prices.get(product):
            return None
            
        window = self.product_parameters[product]["mean_reversion_window"]
        threshold = self.product_parameters[product]["std_dev_threshold"]
        
        if len(self.historical_prices[product]) < window:
            return None
            
        recent_prices = self.historical_prices[product][-window:]
        mean = statistics.mean(recent_prices)
        std_dev = statistics.stdev(recent_prices)
        
        if std_dev == 0:
            return None
            
        z_score = (current_price - mean) / std_dev
        
        # Reversed signals from previous version
        if z_score > threshold:
            return "SELL"  # Price is too high, expect reversion down
        elif z_score < -threshold:
            return "BUY"   # Price is too low, expect reversion up
            
        return None

    def run(self, state: TradingState):
        """Main trading logic"""
        result = {product: [] for product in state.order_depths.keys()}
        
        # Restore state if exists
        if state.traderData != "":
            saved_state = jsonpickle.decode(state.traderData)
            self.historical_prices = saved_state["historical_prices"]
            self.volatility = saved_state.get("volatility", {})
            self.momentum = saved_state.get("momentum", {})
            
        for product in state.order_depths:
            order_depth = state.order_depths[product]
            orders: List[Order] = []
            position = state.position.get(product, 0)
            
            # Skip if no orders on either side
            if not order_depth.buy_orders and not order_depth.sell_orders:
                continue
                
            # Calculate fair value and update metrics
            fair_value = self.calculate_fair_value(product, order_depth)
            if fair_value:
                self.update_market_metrics(product, fair_value)
            
            spread = self.product_parameters[product]["spread"]
            min_edge = self.product_parameters[product]["min_edge"]
            
            if product == "RAINFOREST_RESIN":
                # Market making with dynamic spread based on position
                position_factor = abs(position) / self.product_parameters[product]["position_limit"]
                adjusted_spread = spread + (spread * position_factor)  # Wider spread when position is large
                
                if len(order_depth.sell_orders) > 0:
                    best_ask = min(order_depth.sell_orders.keys())
                    if best_ask < fair_value - adjusted_spread:
                        available_volume = abs(order_depth.sell_orders[best_ask])
                        buy_volume = self.get_order_volume(product, position, "BUY", available_volume)
                        if buy_volume > 0:
                            orders.append(Order(product, best_ask, buy_volume))

                if len(order_depth.buy_orders) > 0:
                    best_bid = max(order_depth.buy_orders.keys())
                    if best_bid > fair_value + adjusted_spread:
                        available_volume = abs(order_depth.buy_orders[best_bid])
                        sell_volume = self.get_order_volume(product, position, "SELL", available_volume)
                        if sell_volume > 0:
                            orders.append(Order(product, best_bid, -sell_volume))
                        
            elif product == "KELP":
                # Combine momentum and mean reversion for KELP
                if product in self.momentum:
                    momentum_signal = self.momentum[product]
                    
                    if momentum_signal > 0.01 and position < self.product_parameters[product]["position_limit"]:
                        # Strong upward momentum - buy
                        if len(order_depth.sell_orders) > 0:
                            best_ask = min(order_depth.sell_orders.keys())
                            if best_ask < fair_value + min_edge:  # Ensure minimum edge
                                available_volume = abs(order_depth.sell_orders[best_ask])
                                buy_volume = self.get_order_volume(product, position, "BUY", available_volume)
                                if buy_volume > 0:
                                    orders.append(Order(product, best_ask, buy_volume))
                    
                    elif momentum_signal < -0.01 and position > -self.product_parameters[product]["position_limit"]:
                        # Strong downward momentum - sell
                        if len(order_depth.buy_orders) > 0:
                            best_bid = max(order_depth.buy_orders.keys())
                            if best_bid > fair_value - min_edge:  # Ensure minimum edge
                                available_volume = abs(order_depth.buy_orders[best_bid])
                                sell_volume = self.get_order_volume(product, position, "SELL", available_volume)
                                if sell_volume > 0:
                                    orders.append(Order(product, best_bid, -sell_volume))
                            
            elif product == "SQUID_INK":
                # Pure mean reversion for SQUID_INK with strict risk management
                signal = self.check_mean_reversion_signal(product, fair_value)
                
                # Only trade if volatility is within acceptable range
                acceptable_volatility = not self.volatility.get(product) or self.volatility[product] < 100
                
                if acceptable_volatility:
                    if signal == "BUY" and len(order_depth.sell_orders) > 0:
                        best_ask = min(order_depth.sell_orders.keys())
                        if best_ask < fair_value + min_edge:  # Ensure minimum edge
                            available_volume = abs(order_depth.sell_orders[best_ask])
                            buy_volume = self.get_order_volume(product, position, "BUY", available_volume)
                            if buy_volume > 0:
                                orders.append(Order(product, best_ask, buy_volume))
                        
                    elif signal == "SELL" and len(order_depth.buy_orders) > 0:
                        best_bid = max(order_depth.buy_orders.keys())
                        if best_bid > fair_value - min_edge:  # Ensure minimum edge
                            available_volume = abs(order_depth.buy_orders[best_bid])
                            sell_volume = self.get_order_volume(product, position, "SELL", available_volume)
                            if sell_volume > 0:
                                orders.append(Order(product, best_bid, -sell_volume))
            
            result[product] = orders

        # Save state
        state_to_save = {
            "historical_prices": self.historical_prices,
            "volatility": self.volatility,
            "momentum": self.momentum
        }
        
        return result, 0, jsonpickle.encode(state_to_save) 